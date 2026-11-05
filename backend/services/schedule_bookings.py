"""Bookings made against a schedule: the staff's click on the calendar and the agent's `book` tool.

They live in the appointments store (mcp_servers/appointments.py) next to the older doctor bookings, so the desk's list,
Google Calendar sync, cancelling and the link to the call and its receipt all work for both. A schedule booking row adds:

    schedule_id, schedule_name, agent_id (the schedule's front desk), start and end (naive local ISO, the schedule's
    timezone in `timezone`), nights (a stay), party, note, source "staff" | "agent" | "walk_in"

and keeps the doctor-booking keys (`doctor`, `specialty`, `weekday`, `reason`) so the older tools still read the file.

Whether a time is free is decided by services/availability.Engine inside the store's transaction, so two bookings of the
same slot cannot both succeed. The schedules file is read without taking its own lock (see services/schedules.py)."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    from services import availability as av, schedules as sch
except ImportError:  # pragma: no cover
    from backend.services import availability as av, schedules as sch

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class BookingRefused(Exception):
    """The time cannot be booked. `result` is the availability answer (reason, nearest options, spoken)."""

    def __init__(self, result: dict):
        super().__init__(result.get("spoken", {}).get("en") or "That time is not available.")
        self.result = result


class BookingProblem(ValueError):
    """Bad input (a name, a phone number) or a booking that cannot be found, worded to be said out loud."""

    def __init__(self, code: str, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.code, self.message, self.field = code, message, field


def _ap():
    from mcp_servers import appointments  # noqa: PLC0415 - loads the MCP SDK
    return appointments


def clean_name(value: Any) -> str:
    """A person's name in any script: Unicode letters and combining marks (Devanagari vowel signs are marks), spaces, dots,
    apostrophes and hyphens; 2 to 80 characters of which at least 2 are letters. Digits, symbols and obvious junk (no
    letters, one letter repeated, starting with a stop or dash) are refused."""
    name = unicodedata.normalize("NFC", re.sub(r"\s+", " ", str(value or ""))).strip()
    letters = [c for c in name if unicodedata.category(c)[0] == "L"]
    allowed = all(unicodedata.category(c)[0] in "LM" or c in " .'-\u2019\u200c\u200d" for c in name)
    junk = len(letters) < 2 or (len(letters) >= 3 and len({c.casefold() for c in letters}) == 1) \
        or unicodedata.category(name[0])[0] != "L" if name else True
    if not 2 <= len(name) <= 80 or not allowed or junk:
        raise BookingProblem("INVALID_NAME", "Please give a name (2 to 80 letters).", "name")
    return name


def clean_phone(value: Any, required: bool) -> str:
    if value is None or str(value).strip() == "":
        if required:
            raise BookingProblem("INVALID_PHONE", "A phone number is needed (10 digits).", "phone")
        return ""
    ap = _ap()
    try:
        return ap._normalize_phone(str(value))   # noqa: SLF001 - one rule for every booking
    except ap.AppointmentError as e:
        raise BookingProblem("INVALID_PHONE", str(e), "phone") from None


# ---------------------------------------------------------------------------------------------- reading

def engine_for(schedule: dict, rows: Optional[List[dict]] = None, now: Optional[datetime] = None) -> av.Engine:
    """The availability engine of a schedule over its current bookings and blocks, and, when Google Calendar is set up, the
    owner's own events (docs/CALENDAR.md)."""
    rows = _ap().all_bookings() if rows is None else rows
    return av.Engine(schedule, av.live_bookings(rows, schedule["id"]), sch.list_blocks(schedule["id"]),
                     now or sch.now(schedule.get("timezone")), external=av.calendar_source(schedule))


def _warm_calendar(schedule: dict, day: Optional[date]) -> None:
    """Read the owner's events of `day` before the appointments file is locked, so a slow Google never holds the lock (the
    answer is remembered for 60 seconds, and a failure is remembered too). Never raises."""
    try:
        source = av.calendar_source(schedule)
        if source is not None and day is not None:
            source(day)
    except Exception:  # noqa: BLE001
        pass


def summary(schedule: dict, rows: Optional[List[dict]] = None) -> dict:
    return engine_for(schedule, rows).summary()


def future_bookings(schedule_id: str, rows: Optional[List[dict]] = None) -> List[dict]:
    """Live bookings of a schedule that have not ended yet."""
    rows = _ap().all_bookings() if rows is None else rows
    schedule = sch.get(schedule_id)
    current = sch.now(schedule.get("timezone") if schedule else None)
    out = []
    for b in rows:
        if b.get("schedule_id") != schedule_id or b.get("status") == "cancelled":
            continue
        try:
            if datetime.fromisoformat(str(b["end"])) > current:
                out.append(b)
        except (KeyError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------------------------- writing

def create(schedule: dict, *, day: date, at: Optional[str], name: Any, phone: Any = None, note: Any = None,
           nights: Optional[int] = None, party: Optional[int] = None, caller: bool = False, source: str = "staff",
           now: Optional[datetime] = None, relation: Any = None, booked_by: Any = None) -> dict:
    """Book a time (`at` "HH:MM" on `day`) or a stay (`day` is the check-in date) and return the stored row. Raises
    BookingRefused when it is not free and BookingProblem for bad input. `caller=True` holds the booking to the minimum
    notice and the horizon (the agent); staff are not held to them."""
    ap = _ap()
    person = clean_name(name)
    digits = clean_phone(phone, required=caller)
    text = re.sub(r"\s+", " ", str(note or "")).strip()[:300]
    if party is not None and (isinstance(party, bool) or not isinstance(party, int) or not 1 <= party <= 500):
        raise BookingProblem("INVALID_PARTY", "The number of people must be from 1 to 500.", "party")
    stay = schedule["kind"] == "nights"
    n = None
    if stay:
        n = 1 if nights is None else nights
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= sch.MAX_NIGHTS:
            raise BookingProblem("INVALID_NIGHTS", f"The number of nights must be from 1 to {sch.MAX_NIGHTS}.", "nights")
    elif at is None:
        raise BookingProblem("INVALID_TIME", "A time is needed.", "start")
    _warm_calendar(schedule, day)
    with ap.transaction() as data:
        current = now or sch.now(schedule.get("timezone"))
        engine = av.Engine(schedule, av.live_bookings(data["bookings"], schedule["id"]),
                           sch.list_blocks(schedule["id"]), current, external=av.calendar_source(schedule))
        result = engine.check(day=day, at=at, nights=n, caller=caller)
        if not result["available"]:
            raise BookingRefused(result)
        start = datetime.fromisoformat(result["requested"]["start"]).replace(tzinfo=None)
        end = datetime.fromisoformat(result["requested"]["end"]).replace(tzinfo=None)
        booking = {
            "booking_id": ap.next_booking_id(data), "name": person, "phone": digits, "doctor": "", "specialty": "",
            "date": start.date().isoformat(), "weekday": WEEKDAYS[start.weekday()], "time": start.strftime("%H:%M"),
            "reason": text, "status": "booked", "created_at": current.isoformat(timespec="seconds"),
            "schedule_id": schedule["id"], "schedule_name": schedule["name"], "agent_id": schedule.get("agent_id"),
            "start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes"),
            "timezone": schedule.get("timezone"), "source": source, "version": 1,
        }
        if stay:
            booking["nights"] = n
        if party is not None:
            booking["party"] = party
        if text:
            booking["note"] = text
        booking.update(_on_behalf(relation, booked_by, person))
        data["bookings"].append(booking)
    ap.sync_booking(booking["booking_id"])   # the calendar, after the booking is saved; a failure never undoes it
    return booking


def _on_behalf(relation: Any, booked_by: Any, person: str) -> Dict[str, str]:
    """`relation` (who the person booked for is to the caller: mother, son, ...) and `booked_by` (the caller's own name)
    when the caller booked for someone else; nothing for a booking for themselves or a name that does not clean up."""
    rel = re.sub(r"[^a-z ]", "", str(relation or "").lower()).strip()[:20]
    if not rel or rel == "self":
        return {}
    out = {"relation": rel}
    try:
        by = clean_name(booked_by) if booked_by else ""
    except BookingProblem:
        by = ""
    if by and by.casefold() != person.casefold():
        out["booked_by"] = by
    return out


def cancel(booking_id: str, agent_id: Optional[str] = None, *, caller: bool = False) -> dict:
    """Cancel a booking against a schedule. With `agent_id` only that front desk's own bookings can be cancelled (the
    agent's tool); a caller cannot cancel one whose time has passed. Raises BookingProblem."""
    ap = _ap()
    wanted = str(booking_id or "").strip().upper().replace(" ", "")
    with ap.transaction() as data:
        row = next((b for b in data["bookings"] if b["booking_id"] == wanted), None)
        if row is None or (agent_id is not None and (not row.get("schedule_id") or row.get("agent_id") != agent_id)):
            raise BookingProblem("NOT_FOUND", f"There is no booking {wanted}.", "booking_id")
        if row["status"] != "booked":
            raise BookingProblem("ALREADY_CANCELLED", f"Booking {wanted} is already cancelled.", "booking_id")
        current = sch.now(row.get("timezone"))
        if caller and datetime.fromisoformat(row.get("end") or row["start"]) <= current:
            raise BookingProblem("IN_THE_PAST", f"Booking {wanted} is in the past and cannot be cancelled.", "booking_id")
        row["status"] = "cancelled"
        row["cancelled_at"] = current.isoformat(timespec="seconds")
        row["version"] = int(row.get("version") or 1) + 1
        out = dict(row)
    note_change(out, "cancelled", at=out["cancelled_at"])
    ap.sync_cancellation(wanted, ap.CALENDAR_WAIT_S if caller else None)
    return out


def note_change(row: dict, kind: str, **fields: Any) -> None:
    """Write a move or a cancellation on the proof of the booking, when it has one (booking_evidence.record_change). The proof
    itself stays as it was sealed. Never raises."""
    if not row.get("evidence_id"):
        return
    try:
        try:
            from services import booking_evidence  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            from backend.services import booking_evidence  # noqa: PLC0415
        booking_evidence.record_change(row["evidence_id"], kind, **fields)
    except Exception:  # noqa: BLE001
        pass


def reschedule(booking_id: str, agent_id: Optional[str] = None, *, day: date, at: Optional[str], nights: Optional[int] = None,
               caller: bool = False, now: Optional[datetime] = None) -> Tuple[dict, dict]:
    """Move a booking to another time, in one transaction, and return (the booking as it is now, the booking as it was).

    The same row is changed in place (same booking_id): date, weekday, time, start and end, and for a stay the nights. The
    new time is checked by the availability engine with this booking itself left out, so moving 3 pm to 3:30 pm is not
    refused for overlapping itself; when it is not free BookingRefused carries the nearest free times and the booking stays
    exactly as it was. `version` goes up by one, `moved_from` {date, time, start} and `moved_at` say what changed, and the
    calendar event is moved after the booking is saved (a failure of Google never undoes the move). The rules for who may
    move which booking are those of `cancel`: `agent_id` keeps it to that front desk's own bookings, `caller=True` holds the
    new time to the minimum notice and the horizon and refuses a booking whose time has passed."""
    ap = _ap()
    wanted = str(booking_id or "").strip().upper().replace(" ", "")
    first = next((b for b in ap.all_bookings() if b["booking_id"] == wanted), None)
    schedule = sch.get(first["schedule_id"]) if first and first.get("schedule_id") else None
    if first is None or schedule is None or (agent_id is not None and first.get("agent_id") != agent_id):
        raise BookingProblem("NOT_FOUND", f"There is no booking {wanted}.", "booking_id")
    if schedule["kind"] != "nights" and at is None:
        raise BookingProblem("INVALID_TIME", "A time is needed.", "start")
    _warm_calendar(schedule, day)
    with ap.transaction() as data:
        row = next((b for b in data["bookings"] if b["booking_id"] == wanted), None)
        if row is None or not row.get("schedule_id") or (agent_id is not None and row.get("agent_id") != agent_id):
            raise BookingProblem("NOT_FOUND", f"There is no booking {wanted}.", "booking_id")
        if row["status"] != "booked":
            raise BookingProblem("ALREADY_CANCELLED", f"Booking {wanted} is already cancelled.", "booking_id")
        current = now or sch.now(row.get("timezone"))
        if caller and datetime.fromisoformat(row.get("end") or row["start"]) <= current:
            raise BookingProblem("IN_THE_PAST", f"Booking {wanted} is in the past and cannot be moved.", "booking_id")
        stay = schedule["kind"] == "nights"
        n = None
        if stay:
            n = nights if nights is not None else row.get("nights") or 1
            if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= sch.MAX_NIGHTS:
                raise BookingProblem("INVALID_NIGHTS", f"The number of nights must be from 1 to {sch.MAX_NIGHTS}.", "nights")
        others = [b for b in data["bookings"] if b["booking_id"] != wanted]
        engine = av.Engine(schedule, av.live_bookings(others, schedule["id"]), sch.list_blocks(schedule["id"]), current,
                           external=av.calendar_source(schedule))
        result = engine.check(day=day, at=at, nights=n, caller=caller)
        if not result["available"]:
            raise BookingRefused(result)
        start = datetime.fromisoformat(result["requested"]["start"]).replace(tzinfo=None)
        end = datetime.fromisoformat(result["requested"]["end"]).replace(tzinfo=None)
        if start.isoformat(timespec="minutes") == str(row.get("start")) and (not stay or n == row.get("nights")):
            raise BookingProblem("SAME_TIME", f"Booking {wanted} is already at that time.", "start")
        previous = dict(row)
        row["moved_from"] = {"date": previous["date"], "time": previous["time"], "start": previous.get("start")}
        row["moved_at"] = current.isoformat(timespec="seconds")
        row["version"] = int(previous.get("version") or 1) + 1
        row["date"], row["weekday"], row["time"] = start.date().isoformat(), WEEKDAYS[start.weekday()], start.strftime("%H:%M")
        row["start"], row["end"] = start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")
        if stay:
            row["nights"] = n
        moved = dict(row)
    note_change(moved, "moved", at=moved["moved_at"], moved_from=moved["moved_from"],
                to={"date": moved["date"], "time": moved["time"], "start": moved["start"]})
    ap.sync_move(wanted, ap.CALENDAR_WAIT_S if caller else None)
    return (ap._find(wanted) or moved), previous   # noqa: SLF001 - the row after the calendar had its say


def find_upcoming(agent_id: str, phone: Any) -> List[dict]:
    """The upcoming bookings of a front desk's schedules made with a phone number, soonest first."""
    digits = clean_phone(phone, required=True)
    out = []
    for b in _ap().all_bookings():
        if b.get("schedule_id") and b.get("agent_id") == agent_id and b.get("status") == "booked" \
                and b.get("phone") == digits:
            try:
                if datetime.fromisoformat(b["end"]) > sch.now(b.get("timezone")):
                    out.append(b)
            except (KeyError, ValueError):
                continue
    out.sort(key=lambda b: b["start"])
    return out


def token_map(rows: Optional[List[dict]] = None, now: Optional[datetime] = None) -> Dict[str, int]:
    """{booking_id: token} for today's and later days' live time-slot bookings: a booking's token for its day is its place
    (1-based) among that day's live bookings of its schedule ordered by start. Computed, never stored, so a cancellation
    renumbers the rest the same way everywhere. Stays have no token."""
    rows = _ap().all_bookings() if rows is None else rows
    groups: Dict[tuple, List[tuple]] = {}
    for b in rows:
        if not b.get("schedule_id") or b.get("status") == "cancelled" or b.get("nights"):
            continue
        try:
            start = datetime.fromisoformat(str(b["start"])).replace(tzinfo=None)
        except (KeyError, TypeError, ValueError):
            continue
        if start.date() < (now or sch.now(b.get("timezone"))).date():
            continue
        groups.setdefault((b["schedule_id"], start.date()), []).append((start, sch._id_num(b["booking_id"]), b["booking_id"]))   # noqa: SLF001
    out: Dict[str, int] = {}
    for items in groups.values():
        for i, (_s, _n, bid) in enumerate(sorted(items), start=1):
            out[bid] = i
    return out


def public(b: dict, tokens: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """A booking as the agent's tools show it: the id, the schedule, when and who (never the phone number). With `tokens`
    (token_map) a booking of today or a later day has its `token`; `booked_by` and `relation` are passed on when the stored
    row has them."""
    out = {"booking_id": b["booking_id"], "schedule": b.get("schedule_name"), "name": b["name"], "date": b["date"],
           "weekday": b.get("weekday"), "time": b["time"], "status": b["status"]}
    if b.get("nights"):
        out["nights"] = b["nights"]
        out["check_out"] = b["end"]
    if tokens and tokens.get(b["booking_id"]):
        out["token"] = tokens[b["booking_id"]]
    for key in ("booked_by", "relation"):
        if b.get(key):
            out[key] = b[key]
    if b.get("moved_from"):
        out["moved_from"] = b["moved_from"]
    return out


def create_walk_in(schedule: dict, *, name: Any, phone: Any = None, note: Any = None, now: Optional[datetime] = None) -> dict:
    """A patient who walked in: staff book them into the first free slot after now today (source "walk_in"), so they have a
    token like everyone else. Raises BookingProblem when the schedule has no free slot left today, and the errors of
    `create`."""
    if schedule["kind"] == "nights":
        raise BookingProblem("NOT_TIME_SLOTS", "Walk-ins are for time-slot schedules.", "schedule_id")
    current = now or sch.now(schedule.get("timezone"))
    engine = engine_for(schedule, now=current)
    slot = next((s for s in engine.slots(current.date()) if s.status in ("free", "booked") and s.remaining > 0), None)
    if slot is None:
        raise BookingProblem("NO_SLOT_LEFT", "There is no free slot left today for a walk-in.", "schedule_id")
    return create(schedule, day=slot.start.date(), at=slot.start.strftime("%H:%M"), name=name, phone=phone, note=note,
                  caller=False, source="walk_in", now=current)


# ---------------------------------------------------------------------------------------------- who a booking is for

SOURCES = ("phone", "missed_call", "web", "staff")


def _mask(phone: Any) -> Optional[str]:
    digits = re.sub(r"\D", "", str(phone or ""))
    return (f"••••{digits[-4:]}" if len(digits) >= 5 else "••••") if digits else None


class Who:
    """Decorates bookings with who they are for, for the owner's screens: `phone_masked` (the last four digits only),
    `source` (phone | missed_call | web | staff), `conversation_id`, `caller_ref` (an opaque id of the caller, see
    services/caller_ref.py) and `caller_bookings_count` (the live bookings made with that number). Build one per request;
    it reads the appointments store once and the conversation of each booking once."""

    def __init__(self, owner: str, rows: Optional[List[dict]] = None, local: bool = True):
        try:
            from services import caller_ref as cr  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            from backend.services import caller_ref as cr  # noqa: PLC0415
        self._cr, self.owner, self.local = cr, owner, local
        self._conv: Dict[str, Optional[dict]] = {}
        self.counts: Dict[str, int] = {}
        for b in (_ap().all_bookings() if rows is None else rows):
            e = cr.e164(b.get("phone"))
            if e and b.get("status") != "cancelled":
                self.counts[e] = self.counts.get(e, 0) + 1

    def conversation(self, conversation_id: Optional[str]) -> Optional[dict]:
        if not conversation_id or not self.local:
            return None
        if conversation_id not in self._conv:
            try:
                from core import state  # noqa: PLC0415
            except ImportError:  # pragma: no cover
                from backend.core import state  # noqa: PLC0415
            try:
                self._conv[conversation_id] = state.local_db.get("conversations", conversation_id, self.owner)
            except Exception:  # noqa: BLE001
                self._conv[conversation_id] = None
        return self._conv[conversation_id]

    def fields(self, phone: Any, source: Any, conversation_id: Optional[str]) -> Dict[str, Any]:
        conv = self.conversation(conversation_id)
        if source in ("staff", "walk_in"):
            kind = "staff"
        else:
            kind = {"missed_call": "missed_call", "web": "web", "phone": "phone"}.get((conv or {}).get("source"), "phone")
        e = self._cr.e164(phone)
        return {"phone_masked": _mask(phone), "source": kind, "conversation_id": conversation_id or None,
                "language": (conv or {}).get("reply_language"),
                "caller_ref": self._cr.caller_ref(self.owner, phone) if e else None,
                "caller_bookings_count": self.counts.get(e) if e else None}

    def for_slot(self, b: dict) -> Dict[str, Any]:
        """The decorate function of availability.Slot.public (`b` is an availability booking dict)."""
        out = self.fields(b.get("phone"), b.get("source"), b.get("conversation_id"))
        out["has_evidence"] = bool(b.get("evidence_id"))
        if b.get("nights"):
            out["nights"] = b["nights"]
        return out
