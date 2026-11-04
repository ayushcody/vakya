"""Running late and the token queue of a time-slot schedule (docs/RUNNING_LATE.md).

    GET  /api/schedules/{id}/today               today's queue: tokens with name, slot, expected time and status, the delay,
                                                 the token now being seen, and the bookings that moved by 15 minutes or more
    POST /api/schedules/{id}/delay               {minutes_late, reason?}: say it is running late today (0 clears it)
    POST /api/schedules/{id}/now-serving         {token}: a number, or "next", is with the doctor now
    POST /api/schedules/{id}/walk-in             {name, phone?, note?}: a patient who walked in, in the first free slot today
    POST /api/schedules/{id}/notify/{booking_id} ring that patient and tell them the new time (409 when calling is not set up)

The state and the estimate are in services/delays.py."""
import asyncio
import logging
from typing import Optional, Union

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

try:
    from core.errors import AppError
    from routers.schedules import fail, schedule_or_404
    from services import delays, phone_greeting, schedule_bookings as sb, schedules as sch
    from services.auth import get_current_user_id
except ImportError:  # pragma: no cover - imported as backend.routers.delays
    from backend.core.errors import AppError
    from backend.routers.schedules import fail, schedule_or_404
    from backend.services import delays, phone_greeting, schedule_bookings as sb, schedules as sch
    from backend.services.auth import get_current_user_id

log = logging.getLogger("vakya.delays")
router = APIRouter(prefix="/api/schedules", tags=["running late"])


class DelayBody(BaseModel):
    minutes_late: int
    reason: Optional[str] = None


class ServingBody(BaseModel):
    token: Union[int, str]


class WalkInBody(BaseModel):
    name: str
    phone: Optional[str] = None
    note: Optional[str] = None


def _phone_module():
    try:
        from routers import phone as px  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.routers import phone as px  # noqa: PLC0415
    return px


def _slots_or_422(row: dict) -> dict:
    if row.get("kind") == "nights":
        raise AppError("NOT_TIME_SLOTS", "Running late and tokens are for time-slot schedules, not stays.", 422)
    return row


def _today(row: dict) -> dict:
    out = delays.queue(row)
    try:
        miss = _phone_module().missing_settings()
    except Exception:  # noqa: BLE001
        miss = ["VOBIZ_AUTH_ID"]
    out["calling"] = {"ready": not miss, "missing": miss}
    return out


@router.get("/{schedule_id}/today")
async def today(schedule_id: str, user_id: str = Depends(get_current_user_id)):
    return await asyncio.to_thread(lambda: _today(_slots_or_422(schedule_or_404(schedule_id))))


@router.post("/{schedule_id}/delay")
async def set_delay(schedule_id: str, body: DelayBody, user_id: str = Depends(get_current_user_id)):
    def work():
        row = _slots_or_422(schedule_or_404(schedule_id))
        try:
            delays.set_delay(row, body.minutes_late, body.reason)
        except sch.ScheduleError as e:
            raise fail(e) from None
        return _today(row)
    return await asyncio.to_thread(work)


@router.post("/{schedule_id}/now-serving")
async def now_serving(schedule_id: str, body: ServingBody, user_id: str = Depends(get_current_user_id)):
    def work():
        row = _slots_or_422(schedule_or_404(schedule_id))
        token = body.token
        if isinstance(token, str) and token.strip().lower() != "next":
            try:
                token = int(token.strip())
            except ValueError:
                raise AppError("INVALID_TOKEN", 'token must be "next" or a number.', 422, {"field": "token"}) from None
        try:
            delays.set_now_serving(row, token, delays.queue(row)["count"])
        except sch.ScheduleError as e:
            raise fail(e) from None
        return _today(row)
    return await asyncio.to_thread(work)


@router.post("/{schedule_id}/walk-in")
async def walk_in(schedule_id: str, body: WalkInBody, user_id: str = Depends(get_current_user_id)):
    def work():
        row = _slots_or_422(schedule_or_404(schedule_id))
        try:
            booking = sb.create_walk_in(row, name=body.name, phone=body.phone, note=body.note)
        except sb.BookingProblem as e:
            raise AppError(e.code, e.message, 409 if e.code == "NO_SLOT_LEFT" else 422, {"field": e.field}) from None
        except sb.BookingRefused as e:
            raise AppError("SLOT_UNAVAILABLE", str(e), 409) from None
        except sch.ScheduleError as e:
            raise fail(e) from None
        return {"booking": sb.public(booking, sb.token_map()), "today": _today(row)}
    return await asyncio.to_thread(work)


@router.post("/{schedule_id}/notify/{booking_id}")
async def notify(schedule_id: str, booking_id: str, request: Request, user_id: str = Depends(get_current_user_id)):
    """Ring the patient of `booking_id` and open the call with the new time. The patient gave this number when booking, which
    counts as agreeing to a call about the booking; the allow-list, the hourly limit and the Vobiz account checks of
    POST /api/phone/call all apply."""
    px = _phone_module()
    row = _slots_or_422(await asyncio.to_thread(schedule_or_404, schedule_id))

    def prepare():
        q = delays.queue(row)
        tok = next((t for t in q["tokens"] if t["booking_id"] == str(booking_id).strip().upper()), None)
        if tok is None:
            raise AppError("NOT_FOUND", f"There is no booking {booking_id} on today's list.", 404, {"field": "booking_id"})
        if not q["minutes_late"] or tok["status"] != "waiting" or tok["moved_minutes"] < delays.AFFECTED_MIN:
            raise AppError("NOTHING_TO_TELL", "This patient's time has not moved by 15 minutes or more, so there is nothing "
                           "to tell them.", 409)
        stored = next((b for b in sb._ap().all_bookings() if b.get("booking_id") == tok["booking_id"]), {})   # noqa: SLF001
        digits = delays._digits(stored.get("phone"))   # noqa: SLF001
        if len(digits) != 10:
            raise AppError("NO_PHONE", "This booking has no phone number to call.", 422)
        return q, tok, "+91" + digits

    q, tok, number = await asyncio.to_thread(prepare)
    miss = px.missing_settings()
    if miss:
        raise AppError("CALLING_NOT_SET_UP", px._not_set_up(miss) + " Until then, tell the patient yourself.", 409,   # noqa: SLF001
                       {"missing": miss})
    if not row.get("agent_id"):
        raise AppError("NO_FRONT_DESK", "This schedule belongs to no front desk, so there is no one to make the call.", 409)
    agent = await px.owned_agent(request, row["agent_id"], user_id)
    say = delays.build_notice(row, q, tok, phone_greeting.business_name(agent))
    e164 = px.normalise_number(number) or number
    delays.remember_notice(e164, row["agent_id"], tok["booking_id"], say)
    try:
        result = await px.place_call(px.CallRequest(agent_id=row["agent_id"], to=number, caller_agreed=True), request, user_id)
    except Exception:
        delays.forget_notice(e164)
        raise
    return {**result, "booking_id": tok["booking_id"], "say": say}
