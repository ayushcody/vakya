"""Bookings: the real appointments in the appointments store, for the desk.

    GET    /api/bookings?from=&to=&agent_id=   appointments whose start date is from..to (YYYY-MM-DD, both inclusive,
                                               both optional), soonest first; `agent_id` keeps those of that front desk
                                               (made on its calls or against its schedules), `schedule_id` those of one schedule
    POST   /api/bookings                       a staff booking against a schedule: {schedule_id, start, name, phone?,
                                               note?, nights?, party?}  ->  {booking}   (409 SLOT_UNAVAILABLE with the
                                               reason and the nearest free times when it is not free)
    DELETE /api/bookings/{id}                  cancel one (and take it off the calendar when it is synced)

The store is the JSON file the agent's appointments tool writes (mcp_servers/appointments.py, VAKYA_APPOINTMENTS_FILE).
A booking row:
    id, name, phone (••••3210: only the last four digits), service (the doctor or partner), specialty, reason,
    start (ISO 8601 with the offset of VAKYA_TIMEZONE, default Asia/Kolkata), date, time, timezone,
    status "booked" | "cancelled", conversation_id and receipt_id (null when the booking was not made on a call),
    agent_id, created_at (with the offset), calendar_sync "synced" | "pending" | "failed" | "not_configured" (services/gcal.py);
    and from the schedules: schedule_id and schedule_name (null for a doctor booking of the older tool), end (ISO; a doctor
    booking lasts 30 minutes), nights (stays), party, note.
"""
import asyncio
import logging
import re
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

try:
    from core.errors import AppError
    from services import booking_evidence, schedule_bookings as sb, schedules as sch
    from services.auth import get_current_user_id
except ImportError:  # pragma: no cover - imported as backend.routers.bookings
    from backend.core.errors import AppError
    from backend.services import booking_evidence, schedule_bookings as sb, schedules as sch
    from backend.services.auth import get_current_user_id

log = logging.getLogger("vakya.bookings")
router = APIRouter(prefix="/api/bookings", tags=["bookings"])

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _store():
    """The appointments module (imported on first use: it loads the MCP SDK)."""
    from mcp_servers import appointments  # noqa: PLC0415
    return appointments


def _zone(ap) -> ZoneInfo:
    try:
        return ZoneInfo(ap.timezone_name())
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Kolkata")


def mask_phone(value: Any) -> Optional[str]:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return None
    return f"••••{digits[-4:]}" if len(digits) >= 5 else "••••"


def _calendar_state(b: dict) -> str:
    sync = b.get("calendar_sync")
    return sync if sync in ("synced", "pending", "failed") else "not_configured"


def public_booking(b: dict, zone: ZoneInfo, who: Optional["sb.Who"] = None) -> Dict[str, Any]:
    """One stored booking as the desk sees it. With `who` (schedule_bookings.Who) the row also says who it is for:
    `phone_masked`, `source` (phone | missed_call | web | staff), `caller_ref`, `caller_bookings_count`, `language`."""
    if b.get("timezone"):   # a booking against a schedule keeps the zone of that schedule
        try:
            zone = ZoneInfo(b["timezone"])
        except ZoneInfoNotFoundError:
            pass
    start = end = None
    try:
        if b.get("start"):
            start = datetime.fromisoformat(str(b["start"])).replace(tzinfo=zone)
        else:
            day = date.fromisoformat(str(b.get("date")))
            h, m = str(b.get("time")).split(":")[:2]
            start = datetime.combine(day, dtime(int(h), int(m)), tzinfo=zone)
        if b.get("end"):
            end = datetime.fromisoformat(str(b["end"])).replace(tzinfo=zone)
        elif start is not None:
            end = start + timedelta(minutes=_store().SLOT_MINUTES)
    except (TypeError, ValueError):
        pass
    created = None
    try:
        created = datetime.fromisoformat(str(b.get("created_at"))).replace(tzinfo=zone).isoformat()
    except (TypeError, ValueError):
        pass
    scheduled = bool(b.get("schedule_id"))
    return {
        "id": b.get("booking_id"), "name": b.get("name"), "phone": mask_phone(b.get("phone")),
        "service": b.get("schedule_name") if scheduled else b.get("doctor"), "specialty": b.get("specialty") or None,
        "reason": b.get("reason") or None,
        "start": start.isoformat() if start else None, "end": end.isoformat() if end else None,
        "date": b.get("date"), "time": b.get("time"), "timezone": getattr(zone, "key", str(zone)),
        "status": "cancelled" if b.get("status") == "cancelled" else "booked",
        "conversation_id": b.get("conversation_id") or None, "receipt_id": b.get("receipt_id") or None,
        "agent_id": b.get("agent_id") or None, "created_at": created,
        "calendar_sync": _calendar_state(b),
        "schedule_id": b.get("schedule_id") or None, "schedule_name": b.get("schedule_name") or None,
        "nights": b.get("nights") or None, "party": b.get("party") or None, "note": b.get("note") or None,
        "evidence_id": b.get("evidence_id") or None, "has_evidence": bool(b.get("evidence_id")),
        **(who.fields(b.get("phone"), b.get("source"), b.get("conversation_id")) if who is not None else {}),
    }


def _day(value: Optional[str], name: str) -> Optional[date]:
    if value is None or value == "":
        return None
    if not _DATE.match(value):
        raise AppError("INVALID_DATE", f"{name} must look like 2026-11-05 (year-month-day).", 422, {"field": name})
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise AppError("INVALID_DATE", f"{name} is not a real date.", 422, {"field": name}) from None


@router.get("")
async def list_bookings(date_from: Optional[str] = Query(None, alias="from", max_length=10),
                        date_to: Optional[str] = Query(None, alias="to", max_length=10),
                        agent_id: Optional[str] = Query(None, max_length=64),
                        schedule_id: Optional[str] = Query(None, max_length=32),
                        caller_ref: Optional[str] = Query(None, max_length=32, pattern=r"^[0-9a-f]{8,32}$"),
                        user_id: str = Depends(get_current_user_id)):
    lo, hi = _day(date_from, "from"), _day(date_to, "to")
    if lo and hi and lo > hi:
        raise AppError("INVALID_DATE", "from is after to.", 422)
    ap = _store()
    zone = _zone(ap)
    rows = await asyncio.to_thread(ap.all_bookings)
    who = await asyncio.to_thread(sb.Who, user_id, rows, True)
    out: List[dict] = []
    for b in rows:
        try:
            day = date.fromisoformat(str(b.get("date")))
        except (TypeError, ValueError):
            continue
        if (lo and day < lo) or (hi and day > hi):
            continue
        if agent_id and b.get("agent_id") != agent_id:
            continue
        if schedule_id and (b.get("schedule_id") or "") != schedule_id.strip().upper():
            continue
        row = public_booking(b, zone, who)
        if caller_ref and row.get("caller_ref") != caller_ref:    # the bookings of one caller (the Callers page)
            continue
        out.append(row)
    out.sort(key=lambda r: (r["start"] or "", r["id"] or ""))
    return {"bookings": out, "timezone": getattr(zone, "key", str(zone))}


class StaffBooking(BaseModel):
    schedule_id: str
    start: str
    name: str
    phone: Optional[str] = None
    note: Optional[str] = None
    nights: Optional[int] = None
    party: Optional[int] = None


@router.post("")
async def create_booking(body: StaffBooking, user_id: str = Depends(get_current_user_id)):
    """A booking made by staff from the calendar. For a time slot `start` is the slot's start; for a stay it is the
    check-in date (the time of day is ignored). Staff are not held to the minimum notice or the horizon."""
    def work():
        schedule = sch.get(body.schedule_id)
        if schedule is None:
            raise AppError("SCHEDULE_NOT_FOUND", "Schedule not found", 404, {"schedule_id": body.schedule_id})
        try:
            start = sch.parse_dt(body.start, schedule.get("timezone"), "start")
            row = sb.create(schedule, day=start.date(), at=start.strftime("%H:%M"), name=body.name, phone=body.phone,
                            note=body.note, nights=body.nights, party=body.party, caller=False, source="staff")
        except sch.ScheduleError as e:
            raise AppError(e.code, e.message, 422, {"field": e.field}) from None
        except sb.BookingProblem as e:
            raise AppError(e.code, e.message, 422, {"field": e.field}) from None
        except sb.BookingRefused as e:
            r = e.result
            raise AppError("SLOT_UNAVAILABLE", str(e), 409, {"reason": r.get("reason"), "before": r.get("before"),
                                                              "after": r.get("after"), "spoken": r.get("spoken")}) from None
        return {"booking": public_booking(row, _zone(_store()))}
    return await asyncio.to_thread(work)


@router.delete("/{booking_id}")
async def cancel_booking(booking_id: str, user_id: str = Depends(get_current_user_id)):
    """Cancel a booking from the desk. Its calendar event, if it has one, is removed; a calendar failure never blocks
    the cancellation (the row then says calendar_sync "failed")."""
    ap = _store()
    try:
        booking = await asyncio.to_thread(ap.cancel_booking, booking_id)
    except ap.AppointmentError as e:
        msg = str(e)
        if "already cancelled" in msg:
            raise AppError("ALREADY_CANCELLED", msg, 409) from None
        raise AppError("NOT_FOUND", "Booking not found", 404, {"booking_id": booking_id}) from None
    booking = await asyncio.to_thread(ap.sync_cancellation, booking["booking_id"]) or booking   # never raises
    return {"booking": public_booking(booking, _zone(ap))}


# ---------------------------------------------------------------------------------------------- proof of booking

def _evidence_id(booking_id: str, user_id: str) -> str:
    """The evidence id of a booking made on a call, when the signed-in owner holds it; else 404 (the same answer for a booking
    that does not exist, has no evidence, or belongs to somebody else)."""
    wanted = str(booking_id or "").strip().upper()
    row = next((b for b in _store().all_bookings() if b.get("booking_id") == wanted), None)
    eid = (row or {}).get("evidence_id")
    if not eid or booking_evidence.owner_of(eid) != user_id:
        raise AppError("NOT_FOUND", "There is no recorded proof for this booking.", 404, {"booking_id": booking_id})
    return eid


@router.get("/{booking_id}/evidence")
async def booking_evidence_of(booking_id: str, user_id: str = Depends(get_current_user_id)):
    """The proof of a booking the agent made on a call, for its owner: the caller's utterance behind each field, the agent's
    read-back and the caller's yes, each with transcript, hashes and whether its audio can be played, and whether the signed
    receipt that covers them verifies (`verified`). Public `/verify` shows the same, redacted, unless `public` is set."""
    def work():
        view = booking_evidence.owner_view(_evidence_id(booking_id, user_id))
        if view is None:
            raise AppError("NOT_FOUND", "There is no recorded proof for this booking.", 404)
        return view
    return await asyncio.to_thread(work)


@router.get("/{booking_id}/evidence/audio/{item_id}")
async def booking_evidence_audio(booking_id: str, item_id: str, user_id: str = Depends(get_current_user_id)):
    def work():
        eid = _evidence_id(booking_id, user_id)
        path = booking_evidence.audio_path(eid, item_id, public=False) if re.fullmatch(r"i\d{1,3}", item_id) else None
        if not path:
            raise AppError("NOT_FOUND", "No recording for this step.", 404)
        return path
    path = await asyncio.to_thread(work)
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})


class PublicBody(BaseModel):
    public: bool


@router.post("/{booking_id}/evidence/public")
async def booking_evidence_public(booking_id: str, body: PublicBody, user_id: str = Depends(get_current_user_id)):
    """Make the proof public (anyone with the receipt can read the transcripts and play the recordings) or private again
    (the default: redacted transcripts and hashes only)."""
    def work():
        eid = _evidence_id(booking_id, user_id)
        booking_evidence.set_public(eid, body.public)
        return booking_evidence.owner_view(eid)
    return await asyncio.to_thread(work)


def install(app) -> None:
    """Called by backend.py: delete evidence recordings past their retention period (and old kept utterances) at start-up."""
    async def _purge() -> None:
        removed = await asyncio.to_thread(booking_evidence.purge_expired)
        if removed["evidence"] or removed["utterances"]:
            log.info("booking evidence: removed %s", removed)
    app.on_event("startup")(_purge)
