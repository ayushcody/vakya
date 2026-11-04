"""Schedules for the desk: what each front desk can be booked for, its calendar of slots, blocks and the availability answer.

    GET    /api/schedules?agent_id=                  schedules (with a `summary` for the card)
    POST   /api/schedules                            create one                                         -> {schedule}
    GET    /api/schedules/{id}                       one schedule                                       -> {schedule}
    PUT    /api/schedules/{id}                       change fields (the ones left out keep their value)  -> {schedule}
    DELETE /api/schedules/{id}[?force=1]             409 HAS_BOOKINGS while future bookings exist; force cancels them
    GET    /api/schedules/{id}/slots?from=&to=       the calendar, at most 42 days (YYYY-MM-DD, inclusive)
    POST   /api/schedules/{id}/blocks                mark a range busy                                  -> {block}
    DELETE /api/schedules/{id}/blocks/{block_id}     free it again
    POST   /api/schedules/{id}/check                 is it free? {date?, time?, nights?, party?, text?}
    POST   /api/schedules/parse-hours                {text} -> a week of ranges read from words

The model, rules and examples are in docs/SCHEDULES.md."""
import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

try:
    from core.errors import AppError
    from services import availability as av, hours_parser, schedule_bookings as sb, schedules as sch, when
    from services.auth import get_current_user_id
except ImportError:  # pragma: no cover - imported as backend.routers.schedules
    from backend.core.errors import AppError
    from backend.services import availability as av, hours_parser, schedule_bookings as sb, schedules as sch, when
    from backend.services.auth import get_current_user_id

log = logging.getLogger("vakya.schedules")
router = APIRouter(prefix="/api/schedules", tags=["schedules"])

MAX_DAYS = 42


def fail(e: sch.ScheduleError) -> AppError:
    return AppError(e.code, e.message, 422, {"field": e.field, **({"info": e.details} if e.details else {})})


def _problem(e: "sb.BookingProblem") -> AppError:
    status = 404 if e.code == "NOT_FOUND" else 409 if e.code in ("ALREADY_CANCELLED", "IN_THE_PAST") else 422
    return AppError(e.code, e.message, status, {"field": e.field})


def schedule_or_404(schedule_id: str) -> dict:
    row = sch.get(schedule_id)
    if row is None:
        raise AppError("SCHEDULE_NOT_FOUND", "Schedule not found", 404, {"schedule_id": schedule_id})
    return row


def with_summary(row: dict, bookings: Optional[List[dict]] = None) -> dict:
    return {**row, "summary": sb.summary(row, bookings)}


class ScheduleBody(BaseModel):
    """Every field is optional here: a create fills the defaults, an update keeps what is left out. The values are checked
    by services/schedules.normalise_schedule."""
    model_config = {"extra": "ignore"}
    agent_id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    kind: Optional[str] = None
    accent: Optional[str] = None
    timezone: Optional[str] = None
    slot_minutes: Optional[int] = None
    buffer_minutes: Optional[int] = None
    capacity: Optional[int] = None
    week: Optional[Dict[str, Any]] = None
    check_in: Optional[str] = None
    check_out: Optional[str] = None
    min_notice_minutes: Optional[int] = None
    horizon_days: Optional[int] = None
    closed_dates: Optional[List[str]] = None


class BlockBody(BaseModel):
    start: str
    end: str
    reason: Optional[str] = None


class CheckBody(BaseModel):
    date: Optional[str] = None
    time: Optional[str] = None
    nights: Optional[int] = None
    party: Optional[int] = None
    text: Optional[str] = None


class ParseHoursBody(BaseModel):
    text: str


@router.get("")
async def list_schedules(agent_id: Optional[str] = Query(None, max_length=64),
                         user_id: str = Depends(get_current_user_id)):
    def build():
        rows = sch.list_schedules(agent_id)
        bookings = sb._ap().all_bookings() if rows else []   # noqa: SLF001
        return [with_summary(r, bookings) for r in rows]
    return {"schedules": await asyncio.to_thread(build)}


@router.post("")
async def create_schedule(body: ScheduleBody, user_id: str = Depends(get_current_user_id)):
    try:
        row = await asyncio.to_thread(sch.create, body.model_dump(exclude_unset=True))
    except sch.ScheduleError as e:
        raise fail(e) from None
    return {"schedule": await asyncio.to_thread(with_summary, row)}


@router.post("/parse-hours")
async def parse_hours(body: ParseHoursBody, user_id: str = Depends(get_current_user_id)):
    """Spoken or typed opening hours as a week of ranges. A parser reads English, Hindi and Marathi words first; a language
    model is asked only when the parser understood nothing. The answer is checked like any week."""
    text = (body.text or "").strip()
    if not text:
        raise AppError("INVALID_TEXT", "Say or type the opening hours.", 422, {"field": "text"})
    if len(text) > 1000:
        raise AppError("INVALID_TEXT", "That is too long (at most 1000 characters).", 422, {"field": "text"})
    return await asyncio.to_thread(hours_parser.parse, text)


@router.get("/{schedule_id}")
async def get_schedule(schedule_id: str, user_id: str = Depends(get_current_user_id)):
    return {"schedule": await asyncio.to_thread(lambda: with_summary(schedule_or_404(schedule_id)))}


@router.put("/{schedule_id}")
async def update_schedule(schedule_id: str, body: ScheduleBody, user_id: str = Depends(get_current_user_id)):
    try:
        row = await asyncio.to_thread(sch.update, schedule_id, body.model_dump(exclude_unset=True))
    except sch.ScheduleError as e:
        raise fail(e) from None
    if row is None:
        raise AppError("SCHEDULE_NOT_FOUND", "Schedule not found", 404, {"schedule_id": schedule_id})
    return {"schedule": await asyncio.to_thread(with_summary, row)}


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: str, force: int = Query(0, ge=0, le=1),
                          user_id: str = Depends(get_current_user_id)):
    def work():
        row = schedule_or_404(schedule_id)
        future = sb.future_bookings(row["id"])
        if future and not force:
            raise AppError("HAS_BOOKINGS", f"{len(future)} upcoming booking(s) use this schedule. Cancel them first, or "
                           "delete with force=1 to cancel them.", 409, {"count": len(future)})
        for b in future:
            try:
                sb.cancel(b["booking_id"])
            except sb.BookingProblem:
                pass
        sch.delete(row["id"])
        return {"deleted": True, "cancelled_bookings": len(future)}
    return await asyncio.to_thread(work)


@router.get("/{schedule_id}/slots")
async def slots(schedule_id: str, date_from: Optional[str] = Query(None, alias="from", max_length=10),
                date_to: Optional[str] = Query(None, alias="to", max_length=10),
                user_id: str = Depends(get_current_user_id)):
    def work():
        row = schedule_or_404(schedule_id)
        try:
            today = sch.now(row.get("timezone")).date()
            a = sch.parse_day(date_from, "from") if date_from else today
            b = sch.parse_day(date_to, "to") if date_to else a + timedelta(days=6)
        except sch.ScheduleError as e:
            raise fail(e) from None
        if b < a:
            raise AppError("INVALID_DATE", "from is after to.", 422)
        if (b - a).days + 1 > MAX_DAYS:
            raise AppError("RANGE_TOO_LARGE", f"Ask for at most {MAX_DAYS} days at a time.", 422)
        return sb.engine_for(row).range_public(a, b, sb.Who(user_id, local=True).for_slot)
    return await asyncio.to_thread(work)


@router.post("/{schedule_id}/blocks")
async def add_block(schedule_id: str, body: BlockBody, user_id: str = Depends(get_current_user_id)):
    def work():
        schedule_or_404(schedule_id)
        try:
            block = sch.add_block(schedule_id, body.model_dump())
        except sch.ScheduleError as e:
            raise fail(e) from None
        if block is None:
            raise AppError("SCHEDULE_NOT_FOUND", "Schedule not found", 404, {"schedule_id": schedule_id})
        return {"block": block}
    return await asyncio.to_thread(work)


@router.delete("/{schedule_id}/blocks/{block_id}")
async def remove_block(schedule_id: str, block_id: str, user_id: str = Depends(get_current_user_id)):
    def work():
        schedule_or_404(schedule_id)
        if not sch.remove_block(schedule_id, block_id):
            raise AppError("BLOCK_NOT_FOUND", "Block not found", 404, {"block_id": block_id})
        return {"deleted": True}
    return await asyncio.to_thread(work)


def resolve_check(row: dict, body: CheckBody) -> dict:
    """The day, time and nights a check asks for: the explicit fields, with `text` read for what they leave out."""
    current = sch.now(row.get("timezone"))
    opening = lambda d: when.opening_ranges(row, d)   # noqa: E731 - "4:30" is read the way that falls in the opening hours
    asked = when.resolve_request(body.text, current, opening) if body.text else when.Request()
    day = asked.date
    at = asked.time.strftime("%H:%M") if asked.time else None
    if body.date:
        try:
            day = sch.parse_day(body.date, "date")
        except sch.ScheduleError:   # "tomorrow", "kal": the date resolver reads it
            day = when.resolve_date(body.date, current).date
            if day is None:
                raise AppError("INVALID_DATE", "date must look like 2026-11-05, or be a day like 'tomorrow'.", 422,
                               {"field": "date"}) from None
    if body.time:
        try:
            at = sch.hhmm(sch.to_min(body.time, "time"))
        except sch.ScheduleError:
            t = when.resolve_time(body.time, when.opening_ranges(row, day)).time
            if t is None:
                raise AppError("INVALID_TIME", "time must look like 18:00.", 422, {"field": "time"}) from None
            at = t.strftime("%H:%M")
    return {"day": day, "at": at, "nights": body.nights if body.nights is not None else asked.nights,
            "party": body.party if body.party is not None else asked.party}


@router.post("/{schedule_id}/check")
async def check(schedule_id: str, body: CheckBody, user_id: str = Depends(get_current_user_id)):
    def work():
        row = schedule_or_404(schedule_id)
        asked = resolve_check(row, body)
        if asked["nights"] is not None and not 1 <= asked["nights"] <= sch.MAX_NIGHTS:
            raise AppError("INVALID_NIGHTS", f"nights must be from 1 to {sch.MAX_NIGHTS}.", 422, {"field": "nights"})
        try:
            return sb.engine_for(row).check(day=asked["day"], at=asked["at"], nights=asked["nights"],
                                            party=asked["party"])
        except sch.ScheduleError as e:
            raise fail(e) from None
    return await asyncio.to_thread(work)
