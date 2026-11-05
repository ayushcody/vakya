"""A local MCP server for the front desk of a clinic or small office: free slots, booking, finding and cancelling appointments.

Runs over stdio, so Vakya (services/tools.py) and any other MCP client (the language model Desktop, MCP Inspector, ...) can use it:

    cd backend && .venv/bin/python -m mcp_servers.appointments

The data is one JSON file (default backend/data/mcp/appointments.json, override with VAKYA_APPOINTMENTS_FILE). It is
created on first use with the fictional doctors and OPD hours of backend/templates/agents/clinic_receptionist.md, so
the tools and the receptionist's knowledge agree; the office template's partners (CA, GST, advocate) are in the same file.
Nobody takes appointments on Sundays (the clinic sees emergency walk-ins only).

Every tool validates its input and raises AppointmentError with a message the caller can say out loud; MCP reports it
as a tool error. Times are in VAKYA_TIMEZONE (default Asia/Kolkata). Slots are 30 minutes long."""
import concurrent.futures
import contextlib
import json
import logging
import os
import re
import sys
import tempfile
import threading
import unicodedata
from datetime import date, datetime, time as dtime, timedelta
from typing import Annotated, List, Optional

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field

try:  # the official MCP SDK: v2 calls the server class MCPServer, v1 called it FastMCP
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # pragma: no cover - depends on the installed SDK major version
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ToolError

try:
    import fcntl  # one booking file shared by several server processes (Vakya and the language model Desktop at once)
except ImportError:  # pragma: no cover - Windows
    fcntl = None

log = logging.getLogger("vakya.appointments")

SLOT_MINUTES = 30
MAX_DAYS_AHEAD = 60
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Weekday numbers (Monday = 0) to the OPD windows of that day, from the clinic template's sample document.
DEFAULT_DOCTORS: List[dict] = [
    {"name": "Dr. Meera Kulkarni", "specialty": "General Physician",
     "hours": {"0": [["09:00", "13:30"]], "2": [["09:00", "13:30"]], "4": [["09:00", "13:30"]]}},
    {"name": "Dr. Rohan Deshmukh", "specialty": "Paediatrician",
     "hours": {"1": [["16:00", "20:00"]], "3": [["16:00", "20:00"]], "5": [["16:00", "20:00"]]}},
    {"name": "Dr. Anjali Rao", "specialty": "Gynaecologist",
     "hours": {"0": [["16:00", "19:00"]], "3": [["16:00", "19:00"]]}},
    {"name": "Dr. Imran Shaikh", "specialty": "Dentist",
     "hours": {"1": [["10:00", "13:00"], ["16:00", "19:00"]], "4": [["10:00", "13:00"], ["16:00", "19:00"]]}},
    {"name": "Dr. Sunita Patil", "specialty": "Dermatologist", "hours": {"5": [["10:00", "13:00"]]}},
]

# The office_front_desk template's partners (templates/agents/office_front_desk.md). They share this store with the
# clinic's doctors but are only found by name or area ("Joshi", "GST", "advocate"): a request with no name still means
# the clinic's doctors, so the clinic receptionist never offers a CA's slots.
OFFICE_STAFF: List[dict] = [
    {"name": "CA Suresh Joshi", "specialty": "Chartered Accountant (income tax, audit)", "group": "office",
     "hours": {str(d): [["10:00", "13:00"], ["15:00", "18:00"]] for d in range(5)}},
    {"name": "CA Pooja Kulkarni", "specialty": "GST and compliance", "group": "office",
     "hours": {str(d): [["11:00", "13:30"], ["15:00", "18:00"]] for d in (0, 2, 4)}},
    {"name": "Adv. Neha Bhosale", "specialty": "Advocate (legal, agreements)", "group": "office",
     "hours": {"1": [["11:00", "13:00"], ["15:00", "17:30"]], "3": [["11:00", "13:00"], ["15:00", "17:30"]],
               "5": [["11:00", "13:00"]]}},
]

DEFAULT_SPECIALTY = "General Physician"  # who a booking without a doctor is for
_ID_FIRST = 10  # booking ids are A10, A11, ...: short enough to say on a call
_thread_lock = threading.RLock()


class AppointmentError(ToolError):
    """A request that cannot be done (bad input, closed day, slot taken). The SDK sends its message to the caller as the
    tool's error (a plain exception would be reported without its text), so it is worded to be said out loud."""


def data_path() -> str:
    return os.getenv("VAKYA_APPOINTMENTS_FILE") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "mcp", "appointments.json")


def timezone_name() -> str:
    return os.getenv("VAKYA_TIMEZONE", "").strip() or "Asia/Kolkata"


def now() -> datetime:
    """The current time in VAKYA_TIMEZONE (default Asia/Kolkata) as a naive datetime, which is how dates and times are
    stored. A function so tests can move the clock."""
    try:
        return datetime.now(ZoneInfo(timezone_name())).replace(tzinfo=None)
    except ZoneInfoNotFoundError:
        return datetime.now()


# ---------------------------------------------------------------------------------------------- storage

def _fresh() -> dict:
    return {"doctors": DEFAULT_DOCTORS + OFFICE_STAFF, "bookings": [], "next_id": _ID_FIRST}


def _read(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("doctors"), list) and isinstance(data.get("bookings"), list):
            data.setdefault("next_id", _ID_FIRST)
            have = {d.get("name") for d in data["doctors"]}
            data["doctors"] += [d for d in OFFICE_STAFF if d["name"] not in have]  # a file from before the office desk
            return data
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        print(f"appointments: {path} is unreadable ({e}); starting a new file", file=sys.stderr)
        with contextlib.suppress(OSError):
            os.replace(path, path + ".corrupt")
    return _fresh()


def _write(path: str, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


@contextlib.contextmanager
def _transaction():
    """Load the data under a lock shared by threads and processes; it is saved when the block ends without error."""
    path = data_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _thread_lock:
        lock_file = open(path + ".lock", "a")
        try:
            if fcntl:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
            existed = os.path.exists(path)
            data = _read(path)
            before = json.dumps(data, sort_keys=True)
            yield data
            if not existed or json.dumps(data, sort_keys=True) != before:  # a read-only tool leaves the file alone
                _write(path, data)
        finally:
            lock_file.close()  # releases the flock


def transaction():
    """The store under its lock, for the schedule bookings (services/schedule_bookings.py): the check that a time is free and
    the write of the booking happen in one transaction."""
    return _transaction()


def next_booking_id(data: dict) -> str:
    """The next booking id (A10, A11, ...) and the counter moved on. Shared by the doctor tools and the schedules."""
    booking_id = f"A{data['next_id']}"
    data["next_id"] += 1
    return booking_id


# ---------------------------------------------------------------------------------------------- validation

def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise AppointmentError("The date must look like 2026-10-16 (year-month-day).") from None


def _parse_time(value: str) -> dtime:
    text = re.sub(r"\s+", " ", str(value).strip().upper().replace(".", ""))
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise AppointmentError("The time must look like 14:30 (24-hour clock).")


def _hhmm(t: dtime) -> str:
    return t.strftime("%H:%M")


def _check_day(day: date) -> None:
    today = now().date()
    if day < today:
        raise AppointmentError(f"{day.isoformat()} is in the past. Appointments are for today or later.")
    if day > today + timedelta(days=MAX_DAYS_AHEAD):
        raise AppointmentError(f"Appointments can be booked up to {MAX_DAYS_AHEAD} days ahead.")
    if day.weekday() == 6:
        raise AppointmentError(f"{day.isoformat()} is a Sunday. We take no appointments on Sundays "
                         "(the clinic sees emergency walk-ins only). Please pick Monday to Saturday.")


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"[\s\-().,]", "", str(value))  # speech recognition may write 9,876,543,210
    if digits.startswith("+"):
        digits = digits[1:]
    if not digits.isdigit():
        raise AppointmentError("The phone number may contain only digits (spaces, dashes and a leading + are fine).")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) != 10:
        raise AppointmentError("The phone number must have 10 digits.")
    return digits


def _clean_name(value: str) -> str:
    name = unicodedata.normalize("NFC", re.sub(r"\s+", " ", str(value))).strip()
    # letters and combining marks of any script (Devanagari vowel signs are marks), spaces, dots, apostrophes, hyphens
    if not 2 <= len(name) <= 80 or sum(unicodedata.category(c)[0] == "L" for c in name) < 2 \
            or unicodedata.category(name[0])[0] != "L" \
            or not all(unicodedata.category(c)[0] in "LM" or c in " .'-\u2019\u200c\u200d" for c in name):
        raise AppointmentError("Please give the patient's name (2 to 80 letters).")
    return name


def _doctor_query(doctor: Optional[str]) -> str:
    """What to look for in a doctor's name or specialty: "Dr. Meera" -> "meera"; "" for no preference ("any doctor")."""
    query = re.sub(r"^dr\.?\s+", "", (doctor or "").strip().lower())
    return "" if query in ("doctor", "dr", "any", "anyone", "any doctor", "a doctor", "none", "null") else query


def _match_doctors(data: dict, doctor: Optional[str]) -> List[dict]:
    """Doctors whose name or specialty contains the text ("meera", "Dr. Imran Shaikh", "dentist"); all when empty."""
    doctors = data["doctors"]
    query = _doctor_query(doctor)
    if not query:
        return [d for d in doctors if d.get("group") != "office"]
    found = [d for d in doctors if query in d["name"].lower() or query in d["specialty"].lower()]
    if not found:
        known = ", ".join(f"{d['name']} ({d['specialty']})" for d in doctors)
        raise AppointmentError(f"No doctor matches '{doctor}'. The doctors are: {known}.")
    return found


def _day_slots(doctor: dict, day: date) -> List[dtime]:
    """All 30-minute slot starts of a doctor's OPD on a day."""
    out: List[dtime] = []
    for start, end in doctor.get("hours", {}).get(str(day.weekday()), []):
        cursor = datetime.combine(day, _parse_time(start))
        stop = datetime.combine(day, _parse_time(end))
        while cursor + timedelta(minutes=SLOT_MINUTES) <= stop:
            out.append(cursor.time())
            cursor += timedelta(minutes=SLOT_MINUTES)
    return out


def _taken(data: dict, doctor: dict, day: date) -> set:
    return {b["time"] for b in data["bookings"]
            if b["status"] == "booked" and b["date"] == day.isoformat() and b["doctor"] == doctor["name"]}


def _free_times(data: dict, doctor: dict, day: date) -> List[str]:
    taken = _taken(data, doctor, day)
    cutoff = now()
    out = []
    for slot in _day_slots(doctor, day):
        if _hhmm(slot) in taken or datetime.combine(day, slot) <= cutoff:
            continue
        out.append(_hhmm(slot))
    return out


def _public(b: dict) -> dict:
    return {k: b[k] for k in ("booking_id", "name", "phone", "doctor", "specialty", "date", "weekday", "time",
                              "reason", "status")}


# ---------------------------------------------------------------------------------------------- Google Calendar
# When GOOGLE_CALENDAR_ID and the service account key are set (services/gcal.py), a booking becomes a calendar event and a
# cancellation removes it. The calendar is never allowed to break a booking: the call is made after the booking is saved, a
# failure is logged and written on the booking (`calendar_sync` "failed" plus `calendar_error`), and the tool waits at most
# CALENDAR_WAIT_S for Google (a caller on the phone must not wait on it); a slower call finishes in the background.
# `calendar_sync` is "synced", "pending" (still being sent), "failed", or absent when no calendar is configured.

CALENDAR_WAIT_S = 2.5
_sync_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="calendar-sync")


def _calendar():
    """The gcal module when a calendar is configured, else None."""
    try:
        from services import gcal  # noqa: PLC0415
    except ImportError:  # pragma: no cover - the backend folder is not importable
        return None
    try:
        return gcal if gcal.configured() else None
    except Exception:  # noqa: BLE001
        return None


def calendar_configured() -> bool:
    return _calendar() is not None


def _set(booking_id: str, **fields) -> None:
    """Write fields on a booking (overwriting). Never raises: this runs after the booking itself is safe."""
    try:
        with _transaction() as data:
            booking = next((b for b in data["bookings"] if b["booking_id"] == booking_id), None)
            if booking is not None:
                for k, v in fields.items():
                    if v is None:
                        booking.pop(k, None)
                    else:
                        booking[k] = v
    except Exception:  # noqa: BLE001
        log.exception("could not record the calendar state of booking %s", booking_id)


def _find(booking_id: str) -> Optional[dict]:
    with _transaction() as data:
        booking = next((b for b in data["bookings"] if b["booking_id"] == booking_id), None)
        return dict(booking) if booking else None


def _event_text(b: dict) -> tuple:
    who = b["name"] + (f" with {b['doctor']}" if b.get("doctor") else f" for {b['schedule_name']}"
                       if b.get("schedule_name") else "")
    lines = [f"Booked by the Vakya receptionist. Booking {b['booking_id']}.", f"Phone: {b['phone']}"]
    if b.get("reason"):
        lines.append(f"Reason: {b['reason']}")
    if b.get("nights"):
        lines.append(f"Nights: {b['nights']}")
    return f"Appointment: {who}", "\n".join(lines)


def _create_event(booking_id: str) -> None:
    gcal = _calendar()
    if gcal is None:
        return
    b = _find(booking_id)
    if b is None or b["status"] != "booked":
        return
    try:
        summary, description = _event_text(b)
        start = datetime.combine(date.fromisoformat(b["date"]), _parse_time(b["time"]))
        minutes = SLOT_MINUTES
        if b.get("end"):   # a booking against a schedule knows how long it lasts (a stay: the nights)
            minutes = max(5, int((datetime.fromisoformat(b["end"]) - datetime.fromisoformat(b["start"])).total_seconds() // 60))
        event_id = gcal.create_event(summary, description, start, minutes, request_id=booking_id)
    except Exception as e:  # noqa: BLE001 - the booking stands whatever Google says
        log.warning("calendar: could not create the event for booking %s: %s", booking_id, e)
        _set(booking_id, calendar_sync="failed", calendar_error=str(e)[:200])
        return
    _set(booking_id, calendar_sync="synced", calendar_event_id=event_id, calendar_error=None)
    again = _find(booking_id)
    if again is not None and again["status"] != "booked":   # cancelled while the event was being made: take it off again
        _delete_event(booking_id)


def _booking_days(b: Optional[dict]) -> List[date]:
    """The calendar days a booking covers (for forgetting what was read of them)."""
    try:
        first = datetime.fromisoformat(str(b.get("start") or f"{b['date']}T{b['time']}")).date()
        last = datetime.fromisoformat(str(b["end"])).date() if b.get("end") else first
        return [first + timedelta(days=i) for i in range(max(0, (last - first).days) + 1)]
    except (KeyError, TypeError, ValueError, AttributeError):
        return []


def _forget(*days: date) -> None:
    """A change made through Vakya: what was read of these days from Google is out of date (services/gcal.py)."""
    try:
        from services import gcal  # noqa: PLC0415
        for d in days:
            gcal.invalidate(d)
    except Exception:  # noqa: BLE001
        pass


def _move_event(booking_id: str) -> None:
    gcal = _calendar()
    if gcal is None:
        return
    b = _find(booking_id)
    if b is None or b["status"] != "booked":
        return
    if not b.get("calendar_event_id"):          # it never reached the calendar (not synced, or failed): make it there now
        _create_event(booking_id)
        return
    try:
        _summary, description = _event_text(b)
        if b.get("moved_from"):
            old = b["moved_from"]
            was = datetime.combine(date.fromisoformat(old["date"]), _parse_time(old["time"]))
            description += f"\nMoved from {was.strftime('%a')} {was.day} {was.strftime('%b')} " \
                           f"{was.hour % 12 or 12}:{was.minute:02d} {'am' if was.hour < 12 else 'pm'}"
        start = datetime.combine(date.fromisoformat(b["date"]), _parse_time(b["time"]))
        minutes = SLOT_MINUTES
        if b.get("end"):
            minutes = max(5, int((datetime.fromisoformat(b["end"]) - datetime.fromisoformat(b["start"])).total_seconds() // 60))
        try:
            gcal.move_event(b["calendar_event_id"], start, minutes, description)
        except Exception as e:  # noqa: BLE001
            if getattr(e, "status", None) in (404, 410):   # the owner deleted the event: put a new one there
                _set(booking_id, calendar_event_id=None)
                _create_event(booking_id)
                return
            raise
    except Exception as e:  # noqa: BLE001 - the move stands whatever Google says
        log.warning("calendar: could not move the event of booking %s: %s", booking_id, e)
        _set(booking_id, calendar_sync="failed", calendar_error=str(e)[:200])
        return
    _set(booking_id, calendar_sync="synced", calendar_error=None)
    again = _find(booking_id)
    if again is not None and again["status"] != "booked":   # cancelled while the event was being moved
        _delete_event(booking_id)


def _delete_event(booking_id: str) -> None:
    b = _find(booking_id)
    event_id = (b or {}).get("calendar_event_id")
    if not event_id:
        return                                              # it never reached the calendar: nothing to remove
    gcal = _calendar()
    try:
        if gcal is None:
            raise RuntimeError("Google Calendar is not set up any more")
        gcal.delete_event(event_id)
    except Exception as e:  # noqa: BLE001
        log.warning("calendar: could not remove the event of booking %s: %s", booking_id, e)
        _set(booking_id, calendar_sync="failed", calendar_error=str(e)[:200])
        return
    _set(booking_id, calendar_sync="synced", calendar_event_id=None, calendar_error=None)


def _run_sync(fn, booking_id: str, wait: float) -> None:
    fut = _sync_pool.submit(fn, booking_id)
    try:
        fut.result(timeout=wait)
    except concurrent.futures.TimeoutError:
        log.info("calendar: booking %s is still being sent to Google; the call goes on", booking_id)
    except Exception:  # noqa: BLE001
        log.exception("calendar sync of booking %s crashed", booking_id)


def sync_booking(booking_id: str, wait: Optional[float] = None) -> None:
    """Put a new booking on the calendar (when one is configured). Never raises."""
    _forget(*_booking_days(_find_quiet(booking_id)))
    if not calendar_configured():
        return
    _set(booking_id, calendar_sync="pending")
    _run_sync(_create_event, booking_id, CALENDAR_WAIT_S if wait is None else wait)


def sync_cancellation(booking_id: str, wait: Optional[float] = None) -> Optional[dict]:
    """Take a cancelled booking off the calendar. Returns the booking as it is afterwards. Never raises. The desk waits
    longer than a caller on the phone does (10 s)."""
    _forget(*_booking_days(_find_quiet(booking_id)))
    _run_sync(_delete_event, booking_id, 10.0 if wait is None else wait)
    return _find(booking_id)


def sync_move(booking_id: str, wait: Optional[float] = None) -> None:
    """Move the event of a booking whose time was just changed (events.patch; the description says "Moved from Thu 6 Nov
    3:00 pm"). A booking with no event yet gets one. The same rules as `sync_booking`: after the move is saved, at most
    `wait` seconds (default CALENDAR_WAIT_S), a failure is written on the booking (`calendar_sync` "failed") and never undoes
    the move, a slow call finishes in the background. Never raises."""
    b = _find_quiet(booking_id)
    _forget(*_booking_days(b), *_booking_days(b.get("moved_from") and {"start": b["moved_from"].get("start"),
                                                                       "date": b["moved_from"].get("date"),
                                                                       "time": b["moved_from"].get("time")}))
    if not calendar_configured():
        return
    _set(booking_id, calendar_sync="pending")
    _run_sync(_move_event, booking_id, CALENDAR_WAIT_S if wait is None else wait)


def _find_quiet(booking_id: str) -> dict:
    try:
        return _find(booking_id) or {}
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------------------------- tools

mcp = _Server("Sunrise Family Clinic appointments")

# Short on purpose: these words go into every prompt of an agent with tools (docs/DOC_QA_EVAL.md, "Fixes after").
DATE = Field(description="YYYY-MM-DD")
TIME = Field(description="HH:MM, 24-hour, on the hour or half hour")
DOCTOR_ANY = Field(description="Doctor's name or specialty; leave out for all")
DOCTOR_DEFAULT = Field(description="Doctor's name or specialty; leave out when the caller names none (books the General "
                                    "Physician)")


@mcp.tool()
def list_free_slots(date: Annotated[str, DATE], doctor: Annotated[Optional[str], DOCTOR_ANY] = None) -> dict:
    """List the free 30-minute slots on a date, for one doctor or all."""
    day = _parse_date(date)
    _check_day(day)
    with _transaction() as data:
        free = [{"doctor": d["name"], "specialty": d["specialty"], "times": _free_times(data, d, day)}
                for d in _match_doctors(data, doctor)]
    free = [row for row in free if row["times"]]
    return {"date": day.isoformat(), "weekday": WEEKDAYS[day.weekday()], "slot_minutes": SLOT_MINUTES, "free": free}


@mcp.tool()
def book_appointment(name: Annotated[str, Field(description="Patient's name")],
                     phone: Annotated[str, Field(description="10 digits")],
                     date: Annotated[str, DATE], time: Annotated[str, TIME],
                     doctor: Annotated[Optional[str], DOCTOR_DEFAULT] = None,
                     reason: Annotated[Optional[str], Field(description="Why they are coming, a few words")] = None
                     ) -> dict:
    """Book a 30-minute appointment. Returns the booking with its booking_id."""
    patient = _clean_name(name)
    digits = _normalize_phone(phone)
    day = _parse_date(date)
    at = _parse_time(time)
    reason_text = re.sub(r"\s+", " ", str(reason or "")).strip()[:200]
    _check_day(day)
    if at.minute % SLOT_MINUTES or at.second:
        raise AppointmentError("Appointments start on the hour or the half hour (for example 10:00 or 10:30).")
    if datetime.combine(day, at) <= now():
        raise AppointmentError("That time has already passed. Please pick a later time.")
    with _transaction() as data:
        candidates = _match_doctors(data, _doctor_query(doctor) or DEFAULT_SPECIALTY)
        on_duty = [d for d in candidates if at in _day_slots(d, day)]
        if not on_duty:
            names = ", ".join(d["name"] for d in candidates)
            raise AppointmentError(f"{names} does not see patients at {_hhmm(at)} on {WEEKDAYS[day.weekday()]}. "
                             "Use list_free_slots to see the available times.")
        free = [d for d in on_duty if _hhmm(at) not in _taken(data, d, day)]
        if not free:
            raise AppointmentError(f"The {_hhmm(at)} slot on {day.isoformat()} is already booked. "
                             "Use list_free_slots to see the free times.")
        chosen = free[0]
        booking = {"booking_id": f"A{data['next_id']}", "name": patient, "phone": digits, "doctor": chosen["name"],
                   "specialty": chosen["specialty"], "date": day.isoformat(), "weekday": WEEKDAYS[day.weekday()],
                   "time": _hhmm(at), "reason": reason_text, "status": "booked",
                   "created_at": now().isoformat(timespec="seconds")}
        data["next_id"] += 1
        data["bookings"].append(booking)
    sync_booking(booking["booking_id"])   # the calendar, after the booking is saved; a failure never undoes it
    return _public(booking)


@mcp.tool()
def find_booking(phone: Annotated[str, Field(description="Phone number the booking was made with")]) -> dict:
    """Find the upcoming bookings made with a phone number."""
    digits = _normalize_phone(phone)
    cutoff = now()
    with _transaction() as data:
        found = [_public(b) for b in data["bookings"]
                 if b["status"] == "booked" and b["phone"] == digits
                 and datetime.combine(date.fromisoformat(b["date"]), _parse_time(b["time"])) > cutoff]
    found.sort(key=lambda b: (b["date"], b["time"]))
    return {"bookings": found}


@mcp.tool()
def cancel_appointment(booking_id: Annotated[str, Field(description="e.g. A12")]) -> dict:
    """Cancel an upcoming booking and free its slot. Ask the caller to confirm first."""
    wanted = str(booking_id).strip().upper().replace(" ", "")
    with _transaction() as data:
        booking = next((b for b in data["bookings"] if b["booking_id"] == wanted), None)
        if booking is None:
            raise AppointmentError(f"There is no booking {wanted}.")
        if booking["status"] != "booked":
            raise AppointmentError(f"Booking {wanted} is already cancelled.")
        if datetime.combine(date.fromisoformat(booking["date"]), _parse_time(booking["time"])) <= now():
            raise AppointmentError(f"Booking {wanted} is in the past and cannot be cancelled.")
        booking["status"] = "cancelled"
        booking["cancelled_at"] = now().isoformat(timespec="seconds")
        booking["version"] = int(booking.get("version") or 1) + 1
    sync_cancellation(booking["booking_id"], CALENDAR_WAIT_S)
    return _public(booking)


# ---------------------------------------------------------------------------------------------- store access (the API)
# Not tools: the desk (routers/bookings.py) reads the same file the tools write, under the same lock.

def all_bookings() -> List[dict]:
    """Every booking in the store (copies), oldest first."""
    with _transaction() as data:
        return [dict(b) for b in data["bookings"]]


def annotate(booking_id: str, **fields) -> bool:
    """Add facts the tool does not know (the conversation, the receipt, the agent) to a booking. True when it exists.
    Only keys with a value are set, and an existing value is never overwritten."""
    wanted = str(booking_id).strip().upper().replace(" ", "")
    with _transaction() as data:
        booking = next((b for b in data["bookings"] if b["booking_id"] == wanted), None)
        if booking is None:
            return False
        for k, v in fields.items():
            if v and not booking.get(k):
                booking[k] = v
    return True


def cancel_booking(booking_id: str) -> dict:
    """Cancel a booking from the desk (any booking that is not cancelled yet, also one whose time has passed).
    Raises AppointmentError when there is no such booking or it is already cancelled."""
    wanted = str(booking_id).strip().upper().replace(" ", "")
    with _transaction() as data:
        booking = next((b for b in data["bookings"] if b["booking_id"] == wanted), None)
        if booking is None:
            raise AppointmentError(f"There is no booking {wanted}.")
        if booking["status"] != "booked":
            raise AppointmentError(f"Booking {wanted} is already cancelled.")
        booking["status"] = "cancelled"
        booking["cancelled_at"] = now().isoformat(timespec="seconds")
        booking["version"] = int(booking.get("version") or 1) + 1
        out = dict(booking)
    return out


def main() -> None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from services.parent_watch import watch_parent
        watch_parent()  # exit when the process that started this server (Vakya, the language model Desktop) goes away
    except ImportError:  # pragma: no cover
        pass
    mcp.run()


if __name__ == "__main__":
    main()
