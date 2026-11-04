"""Schedules: what a front desk can be booked for, when, and how many at once.

A schedule is "Doctor consultations" (time slots) or "Rooms" (nightly stays) of one front desk (agent). This module is the
model and its store; the rules that answer "is this time free?" are in services/availability.py, the bookings made
against a schedule in services/schedule_bookings.py, and the spoken-hours parser in services/hours_parser.py.

    Schedule {id "S1", agent_id, name, description, kind "slots" | "nights", accent, timezone, slot_minutes 5..240,
              buffer_minutes 0..120, capacity 1..500, week {mon..sun: [{start "HH:MM", end "HH:MM"}]},
              check_in, check_out (nights only), min_notice_minutes, horizon_days, closed_dates ["YYYY-MM-DD"],
              created_at, updated_at}
    Block    {id "K1", schedule_id, start, end, reason}          staff marked a range busy (one row per range)

`week` holds the ranges the schedule is open ([] = closed that day); a break is the gap between two ranges. A range may
end at "24:00" (and "23:59" is read as the end of the day). Slots never cross the end of a range, and the next slot starts
`slot_minutes + buffer_minutes` after the one before. For `nights`, a non-empty day means a stay may begin that day.

The store is one JSON file (default backend/data/schedules.json, override with VAKYA_SCHEDULES_FILE), written atomically
under a lock shared by threads and processes. Reads take no lock (a write replaces the file in one step), so the booking
code can read schedules while it holds the appointments lock without any chance of a deadlock.

Times are local to the schedule's timezone (VAKYA_TIMEZONE, default Asia/Kolkata) and handled as naive datetimes; the
API adds the offset when it speaks ISO 8601. Daylight-saving time is not modelled (India has none)."""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
import tempfile
import threading
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

log = logging.getLogger("vakya.schedules")

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
KINDS = ("slots", "nights")
ACCENTS = ("violet", "orange", "blue", "green", "rose", "amber", "teal", "slate")
MAX_RANGES_PER_DAY = 6
MAX_SCHEDULES = 200
MAX_CLOSED_DATES = 400
MAX_NIGHTS = 60

DEFAULTS = {"slot_minutes": 30, "buffer_minutes": 0, "capacity": 1, "min_notice_minutes": 60, "horizon_days": 30}

_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_thread_lock = threading.RLock()


class ScheduleError(ValueError):
    """Input that cannot be a schedule (or a block): `code` is stable, `field` names the offending field."""

    def __init__(self, code: str, message: str, field: Optional[str] = None, details: Any = None):
        super().__init__(message)
        self.code, self.message, self.field, self.details = code, message, field, details


# ---------------------------------------------------------------------------------------------- clock and zones

def timezone_name() -> str:
    return os.getenv("VAKYA_TIMEZONE", "").strip() or "Asia/Kolkata"


def zone(name: Optional[str] = None) -> ZoneInfo:
    try:
        return ZoneInfo(name or timezone_name())
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Kolkata")


def now(tz: Optional[str] = None) -> datetime:
    """The current local time in the zone as a naive datetime. A function so tests can move the clock."""
    return datetime.now(zone(tz)).replace(tzinfo=None)


def iso(dt: datetime, tz: Optional[str] = None) -> str:
    """A naive local datetime as ISO 8601 with the zone's offset."""
    return dt.replace(tzinfo=zone(tz)).isoformat()


def parse_dt(value: Any, tz: Optional[str] = None, field: str = "start") -> datetime:
    """An ISO 8601 string (with or without an offset) as a naive local datetime of the zone. A value with an offset is
    converted into the zone first."""
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ScheduleError("INVALID_DATETIME", f"{field} must be an ISO date and time like 2026-11-05T10:00:00+05:30.",
                            field) from None
    if dt.tzinfo is not None:
        dt = dt.astimezone(zone(tz)).replace(tzinfo=None)
    return dt.replace(second=0, microsecond=0)


def parse_day(value: Any, field: str = "date") -> date:
    if not isinstance(value, str) or not _DATE.match(value.strip()):
        raise ScheduleError("INVALID_DATE", f"{field} must look like 2026-11-05 (year-month-day).", field)
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ScheduleError("INVALID_DATE", f"{field} is not a real date.", field) from None


# ---------------------------------------------------------------------------------------------- times of day

def to_min(value: Any, field: str = "time", allow_24: bool = False) -> int:
    """"HH:MM" as minutes after midnight. "24:00" is 1440 when allowed (the end of a range)."""
    m = _HHMM.match(str(value).strip()) if isinstance(value, str) else None
    if not m:
        raise ScheduleError("INVALID_TIME", f"{field} must look like 09:30 (24-hour HH:MM).", field)
    h, mi = int(m.group(1)), int(m.group(2))
    if mi > 59 or h > 24 or (h == 24 and (mi != 0 or not allow_24)):
        raise ScheduleError("INVALID_TIME", f"{field} must be a real time between 00:00 and 23:59.", field)
    return h * 60 + mi


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def normalise_week(week: Any, field: str = "week") -> Dict[str, List[Dict[str, str]]]:
    """Validate and tidy a week: every day present, ranges sorted by start, "HH:MM" zero-padded, each range ending after it
    starts, no two ranges overlapping (touching is fine). "23:59" as an end becomes "24:00". Raises ScheduleError."""
    if week is None:
        week = {}
    if not isinstance(week, dict):
        raise ScheduleError("INVALID_WEEK", "week must map mon..sun to lists of ranges.", field)
    unknown = [k for k in week if str(k).lower() not in DAYS]
    if unknown:
        raise ScheduleError("INVALID_WEEK", f"Unknown day '{unknown[0]}'. Use mon, tue, wed, thu, fri, sat, sun.", field)
    by_key = {str(k).lower(): v for k, v in week.items()}
    out: Dict[str, List[Dict[str, str]]] = {}
    for d in DAYS:
        ranges = by_key.get(d) or []
        if not isinstance(ranges, list):
            raise ScheduleError("INVALID_WEEK", f"{d} must be a list of {{start, end}} ranges.", f"{field}.{d}")
        if len(ranges) > MAX_RANGES_PER_DAY:
            raise ScheduleError("INVALID_WEEK", f"{d} has more than {MAX_RANGES_PER_DAY} ranges.", f"{field}.{d}")
        parsed = []
        for r in ranges:
            if not isinstance(r, dict) or "start" not in r or "end" not in r:
                raise ScheduleError("INVALID_WEEK", f"Each range of {d} needs a start and an end.", f"{field}.{d}")
            a = to_min(r["start"], f"{d} start")
            b = to_min(r["end"], f"{d} end", allow_24=True)
            if b >= 23 * 60 + 59:
                b = 1440
            if b <= a:
                raise ScheduleError("INVALID_RANGE", f"On {d}, a range must end after it starts "
                                    f"({r['start']} to {r['end']}).", f"{field}.{d}")
            parsed.append((a, b))
        parsed.sort()
        for (a1, b1), (a2, b2) in zip(parsed, parsed[1:]):
            if a2 < b1:
                raise ScheduleError("OVERLAPPING_RANGES", f"On {d}, {hhmm(a1)} to {hhmm(b1)} overlaps {hhmm(a2)} to "
                                    f"{hhmm(b2)}.", f"{field}.{d}")
        out[d] = [{"start": hhmm(a), "end": hhmm(b)} for a, b in parsed]
    return out


def week_minutes(week: Dict[str, list]) -> Dict[int, List[tuple]]:
    """The week as {weekday number (Monday 0): [(start minute, end minute)]} for the engine."""
    return {i: [(to_min(r["start"], allow_24=True), to_min(r["end"], allow_24=True)) for r in week.get(d) or []]
            for i, d in enumerate(DAYS)}


# ---------------------------------------------------------------------------------------------- validation

def _int(payload: dict, key: str, lo: int, hi: int, default: int, label: str) -> int:
    value = payload.get(key, default)
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ScheduleError("INVALID_SCHEDULE", f"{label} must be a whole number from {lo} to {hi}.", key)
    value = int(value)
    if not lo <= value <= hi:
        raise ScheduleError("INVALID_SCHEDULE", f"{label} must be from {lo} to {hi}.", key)
    return value


def _text(value: Any, key: str, limit: int, required: bool = False) -> Optional[str]:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if required and not text:
        raise ScheduleError("INVALID_SCHEDULE", "A schedule needs a name.", key)
    if len(text) > limit:
        raise ScheduleError("INVALID_SCHEDULE", f"{key} is too long (at most {limit} characters).", key)
    return text or None


def normalise_schedule(payload: Any, existing: Optional[dict] = None) -> dict:
    """The schedule a request describes, checked and complete (without id and timestamps). With `existing`, fields the
    request leaves out keep their value. Raises ScheduleError."""
    if not isinstance(payload, dict):
        raise ScheduleError("INVALID_SCHEDULE", "The schedule must be a JSON object.")
    base = dict(existing or {})
    merged = {**base, **{k: v for k, v in payload.items() if k not in ("id", "created_at", "updated_at", "summary")}}
    kind = merged.get("kind", "slots")
    if kind not in KINDS:
        raise ScheduleError("INVALID_SCHEDULE", "kind must be 'slots' or 'nights'.", "kind")
    accent = merged.get("accent") or "violet"
    if accent not in ACCENTS:
        raise ScheduleError("INVALID_SCHEDULE", f"accent must be one of {', '.join(ACCENTS)}.", "accent")
    tz = merged.get("timezone") or timezone_name()
    try:
        ZoneInfo(str(tz))
    except (ZoneInfoNotFoundError, ValueError):
        raise ScheduleError("INVALID_SCHEDULE", f"'{tz}' is not a known timezone.", "timezone") from None
    agent_id = merged.get("agent_id")
    if agent_id is not None and (not isinstance(agent_id, str) or len(agent_id) > 64):
        raise ScheduleError("INVALID_SCHEDULE", "agent_id must be a short string or null.", "agent_id")
    out = {
        "agent_id": agent_id or None,
        "name": _text(merged.get("name"), "name", 80, required=True),
        "description": _text(merged.get("description"), "description", 300),
        "kind": kind,
        "accent": accent,
        "timezone": str(tz),
        "slot_minutes": _int(merged, "slot_minutes", 5, 240, DEFAULTS["slot_minutes"], "The slot length"),
        "buffer_minutes": _int(merged, "buffer_minutes", 0, 120, DEFAULTS["buffer_minutes"], "The gap between bookings"),
        "capacity": _int(merged, "capacity", 1, 500, DEFAULTS["capacity"], "How many at once"),
        "week": normalise_week(merged.get("week")),
        "check_in": None,
        "check_out": None,
        "min_notice_minutes": _int(merged, "min_notice_minutes", 0, 10080, DEFAULTS["min_notice_minutes"], "Minimum notice"),
        "horizon_days": _int(merged, "horizon_days", 1, 365, DEFAULTS["horizon_days"], "How far ahead"),
    }
    if kind == "nights":
        ci = to_min(merged.get("check_in"), "check_in") if merged.get("check_in") else None
        co = to_min(merged.get("check_out"), "check_out") if merged.get("check_out") else None
        if ci is None or co is None:
            raise ScheduleError("INVALID_SCHEDULE", "A nightly schedule needs check_in and check_out times.", "check_in")
        if co > ci:
            raise ScheduleError("INVALID_SCHEDULE", "Check-out must be at or before check-in, so one night does not "
                                "overlap the next.", "check_out")
        out["check_in"], out["check_out"] = hhmm(ci), hhmm(co)
    dates = merged.get("closed_dates") or []
    if not isinstance(dates, list) or len(dates) > MAX_CLOSED_DATES:
        raise ScheduleError("INVALID_SCHEDULE", "closed_dates must be a list of YYYY-MM-DD dates.", "closed_dates")
    out["closed_dates"] = sorted({parse_day(d, "closed_dates").isoformat() for d in dates})
    return out


def normalise_block(schedule: dict, payload: Any) -> dict:
    """A block (start, end, reason) as naive local ISO strings. Raises ScheduleError."""
    if not isinstance(payload, dict):
        raise ScheduleError("INVALID_BLOCK", "The block must be a JSON object with start and end.")
    tz = schedule.get("timezone")
    start = parse_dt(payload.get("start"), tz, "start")
    end = parse_dt(payload.get("end"), tz, "end")
    if end <= start:
        raise ScheduleError("INVALID_BLOCK", "A block must end after it starts.", "end")
    if end - start > timedelta(days=366):
        raise ScheduleError("INVALID_BLOCK", "A block can cover at most a year.", "end")
    return {"start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes"),
            "reason": _text(payload.get("reason"), "reason", 200)}


# ---------------------------------------------------------------------------------------------- storage

def data_path() -> str:
    return os.getenv("VAKYA_SCHEDULES_FILE") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "schedules.json")


def _fresh() -> dict:
    return {"schedules": [], "blocks": [], "next_schedule": 1, "next_block": 1}


def _read(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("schedules"), list) and isinstance(data.get("blocks"), list):
            data.setdefault("next_schedule", 1)
            data.setdefault("next_block", 1)
            return data
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        print(f"schedules: {path} is unreadable ({e}); starting a new file", file=sys.stderr)
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
            data = _read(path)
            before = json.dumps(data, sort_keys=True)
            yield data
            if json.dumps(data, sort_keys=True) != before:
                _write(path, data)
        finally:
            lock_file.close()


def load() -> dict:
    """The whole store, read without a lock."""
    return _read(data_path())


def _stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def list_schedules(agent_id: Optional[str] = None) -> List[dict]:
    rows = [dict(s) for s in load()["schedules"] if agent_id is None or s.get("agent_id") == agent_id]
    rows.sort(key=lambda s: (s.get("created_at") or "", _id_num(s["id"])))
    return rows


def _id_num(value: str) -> int:
    m = re.search(r"\d+", str(value))
    return int(m.group(0)) if m else 0


def get(schedule_id: str) -> Optional[dict]:
    wanted = str(schedule_id or "").strip().upper()
    return next((dict(s) for s in load()["schedules"] if s["id"] == wanted), None)


def create(payload: dict) -> dict:
    clean = normalise_schedule(payload)
    with _transaction() as data:
        if len(data["schedules"]) >= MAX_SCHEDULES:
            raise ScheduleError("TOO_MANY_SCHEDULES", f"At most {MAX_SCHEDULES} schedules.")
        stamp = _stamp()
        row = {"id": f"S{data['next_schedule']}", **clean, "created_at": stamp, "updated_at": stamp}
        data["next_schedule"] += 1
        data["schedules"].append(row)
    return dict(row)


def update(schedule_id: str, payload: dict) -> Optional[dict]:
    """Change a schedule (fields left out keep their value). None when there is no such schedule."""
    wanted = str(schedule_id or "").strip().upper()
    with _transaction() as data:
        row = next((s for s in data["schedules"] if s["id"] == wanted), None)
        if row is None:
            return None
        clean = normalise_schedule(payload, row)
        row.update(clean)
        row["updated_at"] = _stamp()
        out = dict(row)
    return out


def delete(schedule_id: str) -> bool:
    """Remove a schedule and its blocks (the caller deals with bookings first). False when there is no such schedule."""
    wanted = str(schedule_id or "").strip().upper()
    with _transaction() as data:
        if not any(s["id"] == wanted for s in data["schedules"]):
            return False
        data["schedules"] = [s for s in data["schedules"] if s["id"] != wanted]
        data["blocks"] = [b for b in data["blocks"] if b["schedule_id"] != wanted]
    return True


def ids_for_agent(agent_id: str) -> List[str]:
    return [s["id"] for s in list_schedules(agent_id)]


def list_blocks(schedule_id: str) -> List[dict]:
    wanted = str(schedule_id or "").strip().upper()
    rows = [dict(b) for b in load()["blocks"] if b["schedule_id"] == wanted]
    rows.sort(key=lambda b: (b["start"], _id_num(b["id"])))
    return rows


def add_block(schedule_id: str, payload: dict) -> Optional[dict]:
    """Mark a range busy. An identical block that exists already is returned instead of a duplicate. None when there is no
    such schedule."""
    wanted = str(schedule_id or "").strip().upper()
    with _transaction() as data:
        schedule = next((s for s in data["schedules"] if s["id"] == wanted), None)
        if schedule is None:
            return None
        clean = normalise_block(schedule, payload)
        same = next((b for b in data["blocks"] if b["schedule_id"] == wanted and b["start"] == clean["start"]
                     and b["end"] == clean["end"]), None)
        if same is not None:
            return dict(same)
        row = {"id": f"K{data['next_block']}", "schedule_id": wanted, **clean, "created_at": _stamp()}
        data["next_block"] += 1
        data["blocks"].append(row)
    return dict(row)


def remove_block(schedule_id: str, block_id: str) -> bool:
    wb, ws = str(block_id or "").strip().upper(), str(schedule_id or "").strip().upper()
    with _transaction() as data:
        keep = [b for b in data["blocks"] if not (b["id"] == wb and b["schedule_id"] == ws)]
        found = len(keep) != len(data["blocks"])
        data["blocks"] = keep
    return found


# ---------------------------------------------------------------------------------------------- plain words

def _runs(days: List[int]) -> List[List[int]]:
    runs: List[List[int]] = []
    for d in sorted(days):
        if runs and runs[-1][-1] == d - 1:
            runs[-1].append(d)
        else:
            runs.append([d])
    return runs


def _days_phrase(days: List[int]) -> str:
    if len(days) == 7:
        return "Every day"
    parts = []
    for run in _runs(days):
        if len(run) >= 3:
            parts.append(f"{DAY_NAMES[run[0]]} to {DAY_NAMES[run[-1]]}")
        else:
            parts.extend(DAY_NAMES[d] for d in run)
    return ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 2 else " and ".join(parts)


def _ranges_phrase(ranges: List[dict]) -> str:
    if len(ranges) == 1 and ranges[0]["start"] == "00:00" and ranges[0]["end"] == "24:00":
        return "open 24 hours"
    return " and ".join(f"{r['start']} to {r['end']}" for r in ranges)


def hours_text(schedule: dict) -> str:
    """The open hours in plain words: "Mon to Sat, 10:00 to 13:00 and 17:00 to 20:00; Sun, 10:00 to 12:00". Days with the
    same hours are grouped, closed days left out. Nights: "Every day, check-in 12:00, check-out 11:00"."""
    week = schedule.get("week") or {}
    groups: Dict[str, List[int]] = {}
    for i, d in enumerate(DAYS):
        ranges = week.get(d) or []
        if ranges:
            key = json.dumps([[r["start"], r["end"]] for r in ranges])
            groups.setdefault(key, []).append(i)
    if schedule.get("kind") == "nights":
        stay = f"check-in {schedule.get('check_in')}, check-out {schedule.get('check_out')}"
        days = sorted(i for g in groups.values() for i in g)
        return "Closed" if not days else f"{_days_phrase(days)}, {stay}"
    if not groups:
        return "Closed"
    out = []
    for key, days in sorted(groups.items(), key=lambda kv: kv[1][0]):
        ranges = [{"start": a, "end": b} for a, b in json.loads(key)]
        out.append(f"{_days_phrase(days)}, {_ranges_phrase(ranges)}")
    return "; ".join(out)
