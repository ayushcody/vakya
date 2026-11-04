"""The agent's scheduling tools: it asks the same availability code the desk uses, never guesses.

Tools of the built-in "schedules" tool server, run in this process (they need to know which front desk is on the call, so
they are bound to the agent like the phone call controls of services/call_control.py). An agent gets them when "schedules"
is in its `tools`, or when it has schedules (services/schedules.py):

    check_availability(schedule?, date, time?, nights?)   is it free? when not: why, and the nearest free times either side
    list_free_slots(schedule?, date)                      the free times on a day (a stay: rooms left on the coming nights)
    book(schedule?, date, time?, nights?, name, phone)    book it (the turn loop makes the agent read it back first)
    cancel(booking_id)                                    cancel one of this front desk's own bookings
    find_booking(phone)                                   the caller's upcoming bookings (to cancel or change one)
    queue_status(name?, phone?, schedule?)                today's token, how many are ahead, the expected time and the delay
                                                          (services/delays.py; see docs/RUNNING_LATE.md)

`schedule` is matched by name, loosely and in Hindi or English; with one schedule it can be left out. Dates and times arrive
already worked out from the caller's words (services/when.py). Every answer comes from services/availability.Engine, the
code behind /api/schedules/{id}/check, so the phone and the desk agree."""
from __future__ import annotations

import difflib
import re
import unicodedata
from datetime import date, datetime
from typing import Any, List, Optional, Tuple

try:
    from services import availability as av, delays, schedule_bookings as sb, schedules as sch
except ImportError:  # pragma: no cover
    from backend.services import availability as av, delays, schedule_bookings as sb, schedules as sch

SERVER = "schedules"
LABEL = "Schedules (check availability, book and cancel on the front desk's own schedules)"

_SCHEDULE = {"type": "string", "description": "Schedule name; leave out if there is only one"}
_DATE = {"type": "string", "description": "YYYY-MM-DD"}
_TIME = {"type": "string", "description": "HH:MM, 24-hour"}
_NIGHTS = {"type": "integer", "description": "Number of nights (stays)", "minimum": 1, "maximum": sch.MAX_NIGHTS}

TOOLS = [
    {"tool": "check_availability",
     "description": "Check whether a time (or a stay) is free. If it is not, the result has the nearest free times before "
                    "and after: offer them.",
     "schema": {"type": "object", "properties": {"schedule": _SCHEDULE, "date": _DATE, "time": _TIME, "nights": _NIGHTS},
                "required": ["date"]}},
    {"tool": "list_free_slots",
     "description": "List the free times on a date (a stay: the rooms left on the coming nights).",
     "schema": {"type": "object", "properties": {"schedule": _SCHEDULE, "date": _DATE}, "required": ["date"]}},
    {"tool": "book",
     "description": "Book a time or a stay. Use it only after check_availability said it is free.",
     "schema": {"type": "object",
                "properties": {"schedule": _SCHEDULE, "date": _DATE, "time": _TIME, "nights": _NIGHTS,
                               "name": {"type": "string", "description": "Name of the person the booking is for"},
                               "phone": {"type": "string", "description": "10 digits"},
                               "relation": {"type": "string", "description": "how the person booked for is related to the caller (mother, father, son, daughter, wife, husband, self, ...); omit when the caller books for themselves"},
                               "booked_by": {"type": "string", "description": "the caller's own name, only when booking for someone else"}},
                "required": ["date", "name", "phone"]}},
    {"tool": "cancel",
     "description": "Cancel an upcoming booking and free its time. Ask the caller to confirm first.",
     "schema": {"type": "object", "properties": {"booking_id": {"type": "string", "description": "e.g. A12"}},
                "required": ["booking_id"]}},
    {"tool": "find_booking",
     "description": "Find the upcoming bookings made with a phone number.",
     "schema": {"type": "object", "properties": {"phone": {"type": "string", "description": "10 digits"}},
                "required": ["phone"]}},
    {"tool": "queue_status",
     "description": "Where the caller stands in today's queue: their token, how many patients are ahead, about when they will "
                    "be seen, and how late the doctor is running. Use it for 'how many before me', 'has the doctor come', "
                    "'is he late'. Pass the phone number the booking was made with (best) and/or the caller's name.",
     "schema": {"type": "object",
                "properties": {"name": {"type": "string", "description": "Caller's name"},
                               "phone": {"type": "string", "description": "10 digits, the number the booking was made with"},
                               "schedule": _SCHEDULE}}},
]

# Words callers use for what a schedule is called, mapped to the English the names are usually written in.
_SYNONYMS = {"डॉक्टर": "doctor", "डाक्टर": "doctor", "कमरा": "room", "कमरे": "rooms", "रूम": "room", "टेबल": "table",
             "सैलून": "salon", "अपॉइंटमेंट": "appointment", "अपॉइन्टमेंट": "appointment", "परामर्श": "consultation",
             "कंसल्टेशन": "consultation", "जिम": "gym", "ट्रायल": "trial", "सर्विस": "service", "सेवा": "service"}
_NOISE = {"the", "a", "an", "of", "for", "my", "your", "ka", "ki", "ke", "का", "की", "के", "me", "से"}


def _tokens(text: str) -> set:
    low = unicodedata.normalize("NFC", str(text or "")).lower()
    words = re.findall(r"[\w" + "ऀ-ॿ" + r"]+", low)
    return {_SYNONYMS.get(w, w).rstrip("s") for w in words if w not in _NOISE}


def resolve_schedule(agent_id: str, name: Any) -> Tuple[Optional[dict], Optional[str]]:
    """(schedule, None), or (None, what to tell the model) when there is none or it cannot tell which."""
    rows = sch.list_schedules(agent_id)
    if not rows:
        return None, "This front desk has no schedules to book into."
    wanted = str(name or "").strip()
    if not wanted or wanted.lower() in ("any", "null", "none", "default", "anything"):
        if len(rows) == 1:
            return rows[0], None
        return None, "There is more than one schedule, so ask which one the caller means: " + \
            "; ".join(r["name"] for r in rows) + "."
    wt = _tokens(wanted)
    scored = []
    for r in rows:
        rt = _tokens(r["name"])
        if wanted.lower() == r["name"].lower():
            score = 1.0
        elif wanted.lower() in r["name"].lower() or r["name"].lower() in wanted.lower():
            score = 0.9
        else:
            overlap = len(wt & rt) / len(wt | rt) if wt | rt else 0.0
            ratio = difflib.SequenceMatcher(None, wanted.lower(), r["name"].lower()).ratio()
            score = max(overlap * 1.1, ratio * 0.8)
        scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    if scored[0][0] >= 0.5 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.15 or scored[0][0] >= 0.9):
        return scored[0][1], None
    if len(rows) == 1:
        return rows[0], None
    return None, f"No schedule is called '{wanted}'. The schedules are: " + "; ".join(r["name"] for r in rows) + "."


# ---------------------------------------------------------------------------------------------- answers

def _human(value: Optional[dict], nights: bool = False) -> Optional[dict]:
    """{"date", "weekday", "time"} of a free option, for the model."""
    if not value:
        return None
    start = datetime.fromisoformat(value["start"]).replace(tzinfo=None)
    out = {"date": start.date().isoformat(), "weekday": av.WEEKDAYS_EN[start.weekday()]}
    if not nights:
        out["time"] = start.strftime("%H:%M")
    return out


def _check(schedule: dict, args: dict) -> Tuple[bool, Any]:
    day = _date(args.get("date"))
    at = _time(args.get("time"))
    engine = sb.engine_for(schedule)
    stay = schedule["kind"] == "nights"
    res = engine.check(day=day, at=at, nights=args.get("nights") if stay else None, caller=True)
    out = {"schedule": schedule["name"], "available": res["available"], "reason": res["reason"],
           "date": day.isoformat() if day else None, "time": at}
    if stay:
        out["nights"] = res.get("nights") or 1
    if res.get("requested"):
        out["weekday"] = av.WEEKDAYS_EN[datetime.fromisoformat(res["requested"]["start"]).weekday()]
    if not res["available"]:
        out["before"], out["after"] = _human(res["before"], stay), _human(res["after"], stay)
        out["alternatives"] = [x for x in (out["before"], out["after"]) if x]
    out["say"] = res["spoken"]
    if res["available"]:
        out["next_step"] = "Read the day, date and time back, ask 'Shall I book it?', and call book after a yes."
    return True, out


def _list(schedule: dict, args: dict) -> Tuple[bool, Any]:
    day = _date(args.get("date"))
    if day is None:
        return False, "Which date? Pass the date as YYYY-MM-DD."
    engine = sb.engine_for(schedule)
    out = {"schedule": schedule["name"], "date": day.isoformat(), "weekday": av.WEEKDAYS_EN[day.weekday()]}
    if schedule["kind"] == "nights":
        rooms = []
        for i in range(7):
            d = day + (date.resolution * i)
            for s in engine.slots(d):
                if s.status in ("free", "booked") and s.remaining > 0:
                    rooms.append({"date": d.isoformat(), "rooms_left": s.remaining})
        out["free"] = rooms
        return True, out
    times = engine.free_times(day)
    out.update({"slot_minutes": schedule["slot_minutes"], "count": len(times), "free": times[:16]})
    if len(times) > 16:
        out["more"] = f"{len(times) - 16} later times not listed"
    if not times:
        res = engine.check(day=day, at=None)
        out["say"] = res["spoken"]
        if res.get("after"):
            out["next_free"] = _human(res["after"])
        out["reason"] = "closed" if not engine.is_open(day) else "no free time"
    return True, out


def _book(schedule: dict, agent_id: str, args: dict) -> Tuple[bool, Any]:
    day = _date(args.get("date"))
    if day is None:
        return False, "Which date? Pass the date as YYYY-MM-DD."
    stay = schedule["kind"] == "nights"
    at = _time(args.get("time"))
    if not stay and at is None:
        return False, "Which time? Pass the time as HH:MM."
    try:
        row = sb.create(schedule, day=day, at=at, name=args.get("name"), phone=args.get("phone"),
                        nights=args.get("nights") if stay else None, caller=True, source="agent",
                        relation=args.get("relation"), booked_by=args.get("booked_by"))
    except sb.BookingRefused as e:
        r = e.result
        before, after = _human(r["before"], stay), _human(r["after"], stay)
        return False, {"booked": False, "reason": r["reason"], "say": r["spoken"],
                       "alternatives": [x for x in (before, after) if x]}
    except sb.BookingProblem as e:
        return False, e.message
    except sch.ScheduleError as e:
        return False, e.message
    out = sb.public(row, sb.token_map())
    out["say"] = {"en": f"Booked. The booking id is {row['booking_id']}.", "hi": f"बुकिंग हो गई। बुकिंग आईडी {row['booking_id']} है।"}
    if not stay and day == sch.now(schedule.get("timezone")).date():
        late = delays.running_late_note(schedule)
        if late:
            out["running_late"] = late
    return True, out


def _cancel(agent_id: str, args: dict) -> Tuple[bool, Any]:
    try:
        return True, sb.public(sb.cancel(args.get("booking_id"), agent_id, caller=True))
    except sb.BookingProblem as e:
        return False, e.message


def _find(agent_id: str, args: dict) -> Tuple[bool, Any]:
    try:
        rows = sb.find_upcoming(agent_id, args.get("phone"))
    except sb.BookingProblem as e:
        return False, e.message
    tokens = sb.token_map()
    return True, {"bookings": [sb.public(b, tokens) for b in rows]}


def _date(value: Any) -> Optional[date]:
    try:
        return sch.parse_day(value, "date") if value else None
    except sch.ScheduleError:
        return None


def _time(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        return sch.hhmm(sch.to_min(value, "time"))
    except sch.ScheduleError:
        return None


def run(tool: str, args: dict, agent_id: str) -> Tuple[bool, Any]:
    """Blocking: do `tool` for the front desk `agent_id`. Returns (ok, value); a failure's value is a sentence."""
    try:
        if tool == "cancel":
            return _cancel(agent_id, args)
        if tool == "find_booking":
            return _find(agent_id, args)
        if tool == "queue_status":
            try:
                return delays.queue_status(agent_id, args.get("name"), args.get("phone"), args.get("schedule"))
            except sb.BookingProblem as e:
                return False, e.message
        schedule, problem = resolve_schedule(agent_id, args.get("schedule"))
        if schedule is None:
            return False, problem
        if tool in ("check_availability", "list_free_slots", "book") and not args.get("date"):
            return False, "Which date? Pass the date as YYYY-MM-DD."
        if tool in ("check_availability", "list_free_slots", "book") and _date(args.get("date")) is None:
            return False, f"I could not read the date '{args.get('date')}'. Pass it as YYYY-MM-DD."
        if tool == "check_availability":
            return _check(schedule, args)
        if tool == "list_free_slots":
            return _list(schedule, args)
        if tool == "book":
            return _book(schedule, agent_id, args)
        return False, f"There is no tool called '{tool}'."
    except sch.ScheduleError as e:
        return False, e.message
    except Exception as e:  # noqa: BLE001 - a tool never takes the turn down
        return False, f"the scheduling tool failed ({type(e).__name__})"


def opening_for(agent_id: str, args: Any):
    """The opening hours the time a caller named is read against (services/when.py: "4:30" at a clinic open 9 to 13 and
    16 to 20 is 4:30 pm): a function of the date, over the schedule the call names (or the only one)."""
    try:
        schedule, _ = resolve_schedule(agent_id, (args or {}).get("schedule") if isinstance(args, dict) else None)
    except Exception:  # noqa: BLE001
        return None
    if schedule is None or schedule.get("kind") == "nights":
        return None
    try:
        from services import when  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.services import when  # noqa: PLC0415
    return lambda d=None: when.opening_ranges(schedule, d)


def has_schedules(agent_id: Optional[str]) -> bool:
    return bool(agent_id) and any(s.get("agent_id") == agent_id for s in sch.load()["schedules"])


def specs_for_agent(agent_id: str, tools_mod: Any) -> List[Any]:
    """The tool specs of an agent, bound to its id."""
    out = []
    for d in TOOLS:
        spec = tools_mod.ToolSpec(SERVER, d["tool"], d["description"], d["schema"])
        spec.runner = lambda tool, args, _a=agent_id: run(tool, args, _a)
        spec.opening_for = lambda args, _a=agent_id: opening_for(_a, args)
        out.append(spec)
    return out
