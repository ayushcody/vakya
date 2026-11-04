"""Calls and bookings: what the agents have talked about, read from what is already stored. Nothing here writes.

    GET /api/calls?agent_id=&days=30&limit=50      the owner's recent conversations, newest first
        &from=YYYY-MM-DD&to=YYYY-MM-DD              instead of `days`: whole days, both ends included, in VAKYA_TIMEZONE
        &range=today|yesterday|7d|30d               the same, by name (the page's Today / Yesterday / Last 7 days / Last 30 days)
        &hide_empty=true                            leave out calls where nothing was said (counted in `empty_hidden`)
    GET /api/calls/{conversation_id}               one conversation with its transcript (and what the tools did) and
                                                   `details`: what the caller told the agent (services/caller_memory.py),
                                                   the number and e-mail masked

Where the facts come from (written by backend.turn_store, routers/phone.py and routers/web_call.py; local JSON
store or Supabase, chosen like every other route by the `Storage-Mode` header):
  * channel: `conversations.source` is "phone", "missed_call" (a phone call the receptionist placed to call back a missed
    call: channel "phone" and `called_back` true) or "web" (a web call link visitor); no source means the owner used the
    app (Talk to your agent, or a Test call, which is also flagged `is_test`);
  * caller: a call does not store the caller's number, so it is None.
    Numbers are always masked ("••••3210"), also inside the transcript text;
  * duration: phone calls only, from the first turn's conversation row to the end of the last reply (a close
    estimate: the call itself is not timed anywhere);
  * outcome, derived and never guessed:
      booked          a booking tool call succeeded (appointments); `booking` then holds {id, date, time, with};
      handed_off      the caller asked for a person (`needs_human`);
      ended_early     a phone call where the caller never got past the greeting;
      couldnt_answer  the agent said it did not know (services/knowledge_gaps.is_gap_reply) or its model failed;
      answered        everything else.
"""
import asyncio
import builtins
import logging
import os
import re
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, List, Optional

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Query, Request

try:
    from core.errors import AppError
    from services import caller_memory
    from services import knowledge_gaps as kg
    from services.auth import get_current_user_id
except ImportError:  # pragma: no cover - imported as backend.routers.conversations
    from backend.core.errors import AppError
    from backend.services import caller_memory
    from backend.services import knowledge_gaps as kg
    from backend.services.auth import get_current_user_id

log = logging.getLogger("vakya.calls")
router = APIRouter(prefix="/api/calls", tags=["calls"])


def _b():
    """The running backend (core/runtime.py): every name is read from the module that owns it, at call time."""
    try:
        from core.runtime import runtime  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.core.runtime import runtime  # noqa: PLC0415
    return runtime

CHANNELS = {"phone": "phone", "missed_call": "phone", "web": "website"}
PHONE_SOURCES = ("phone", "missed_call")   # "missed_call": the receptionist rang a caller back after a missed call
LABELS = {
    "answered": "Answered", "booked": "Booked", "handed_off": "Needs a person", "couldnt_answer": "Couldn't answer",
    "ended_early": "Ended early",
}
BOOK, CANCEL_BOOKING = ("book_appointment", "book"), ("cancel_appointment", "cancel")   # the doctor tools, then the schedule tools
MAX_CANDIDATES = 500          # conversations looked at per request
MAX_CALL_SECONDS = 4 * 3600   # a longer span is not a call (the row was reused or the clock is off)
PREVIEW_CHARS = 140

# ------------------------------------------------------------------------------------------------------ masking

_PHONE = re.compile(r"(?<![\d-])(?:\+?91[ -]?)?[6-9]\d{4}[ -]?\d{5}(?![\d-])")
_DIGITS = re.compile(r"(?<![\d-])\d{7,}(?![\d-])")


def mask_number(value: Any) -> Optional[str]:
    """`••••3210` for a stored phone number; None when there is no number."""
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return None
    return f"••••{digits[-4:]}" if len(digits) >= 5 else "••••"


def mask_text(text: Any) -> str:
    """The text with every phone number or long digit run reduced to its last four digits."""
    def one(m: "re.Match[str]") -> str:
        return mask_number(m.group(0)) or "••••"
    return _DIGITS.sub(one, _PHONE.sub(one, str(text or "")))


def caller_details(B, request: Request, conversation_id: str, user_id: str) -> Dict[str, str]:
    """What the caller told the agent in this conversation, for the owner: the number and the e-mail address masked like
    the transcript's. {} when nothing was kept (or CALLER_MEMORY was off)."""
    db = B.local_db if B.storage_mode_of(request) == "local" else None
    raw = caller_memory.load(conversation_id, db, user_id)
    form = caller_memory.public_view(raw)
    if raw.get("phone"):
        form["phone"] = mask_number(raw["phone"]) or ""
    return {k: v for k, v in form.items() if v}


# ------------------------------------------------------------------------------------------------------ derivation

def _ts(value: Any) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _tool_calls(turn: dict) -> List[dict]:
    calls = turn.get("tool_calls")
    return [c for c in calls if isinstance(c, dict)] if isinstance(calls, list) else []


_BOOKING_ID = re.compile(r"\bbooking\s+([A-Za-z]\d{1,5})\b", re.I)
_BOOKING_WHEN = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ T,]+(\d{1,2}:\d{2}))?")
_BOOKING_WITH = re.compile(r"\bwith\s+(.+?)\s+on\s", re.I)
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def parse_booking(summary: str) -> Optional[Dict[str, Optional[str]]]:
    """{id, date, time, with} from a booking tool's answer ("booking a56 on thursday 2026-11-12 19:00"); None when it
    names no booking id. The id is upper case (A56); date is YYYY-MM-DD and time HH:MM when the answer has them."""
    m = _BOOKING_ID.search(summary)
    if not m:
        return None
    when = _BOOKING_WHEN.search(summary)
    who = _BOOKING_WITH.search(summary)
    return {"id": m.group(1).upper(), "date": when.group(1) if when else None,
            "time": when.group(2) if when and when.group(2) else None, "with": mask_text(who.group(1)) if who else None}


def booking_words(b: Dict[str, Optional[str]]) -> str:
    """"A56 · Thu 12 Nov, 7 pm": the id, then the day and the time in 12-hour words (the times are already local)."""
    parts: List[str] = []
    try:
        d = date.fromisoformat(b.get("date") or "")
        parts.append(f"{_WEEKDAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}")
    except ValueError:
        pass
    t = b.get("time") or ""
    if re.fullmatch(r"\d{1,2}:\d{2}", t):
        h, mi = (int(x) for x in t.split(":"))
        if 0 <= h < 24 and 0 <= mi < 60:
            clock = f"{h % 12 or 12}{'' if mi == 0 else f':{mi:02d}'} {'am' if h < 12 else 'pm'}"
            parts.append(clock)
    when = ", ".join(parts)
    return f"{b['id']} · {when}" if when else str(b["id"])


def _booked(turns: List[dict]) -> Optional[str]:
    """The answer of the last successful booking tool call that no cancellation undid (turns oldest first)."""
    booked: Optional[str] = None
    for t in turns:
        for c in _tool_calls(t):
            if not c.get("ok"):
                continue
            name, summary = c.get("name"), str(c.get("result") or "")
            if name in BOOK:
                booked = mask_text(summary)
            elif name in CANCEL_BOOKING:
                booked = None
    return booked


def derive_outcome(source: Optional[str], needs_human: bool, turns: List[dict]) -> Dict[str, Any]:
    """{key, label, detail} for one conversation, from its stored turns (oldest first)."""
    booked = _booked(turns)
    if booked:
        parsed = parse_booking(booked)
        return {"key": "booked", "label": LABELS["booked"],
                "detail": f"Booking {booking_words(parsed)}" if parsed else booked.capitalize()}
    replies = [t for t in turns if t.get("role") == "agent"]
    if needs_human:
        return {"key": "handed_off", "label": LABELS["handed_off"], "detail": "The caller asked for a person."}
    if source in PHONE_SOURCES and len(replies) <= 1:
        return {"key": "ended_early", "label": LABELS["ended_early"],
                "detail": "The caller hung up after the greeting." if replies else "The call connected but nothing was said."}
    question: Optional[str] = None
    for t in turns:
        if t.get("role") == "user":
            question = t.get("text_content")
        elif t.get("role") == "agent" and (t.get("fallback") or kg.is_gap_reply(t.get("text_content") or "")):
            what = "The agent had a technical problem" if t.get("fallback") else "The agent said it did not know"
            ask = kg.redact(question or "")
            return {"key": "couldnt_answer", "label": LABELS["couldnt_answer"],
                    "detail": f"{what}{': ' + mask_text(ask) if ask else '.'}"}
    return {"key": "answered", "label": LABELS["answered"], "detail": None}


def _duration_s(conv: dict, turns: List[dict]) -> Optional[int]:
    if conv.get("source") not in PHONE_SOURCES or not turns:
        return None
    start, last = _ts(conv.get("created_at")), turns[-1]
    end = _ts(last.get("created_at"))
    if start is None or end is None:
        return None
    seconds = (end - start).total_seconds() + (last.get("total_ms") or last.get("latency_ms") or 0) / 1000.0
    return int(seconds) if 0 < seconds <= MAX_CALL_SECONDS else None


def _preview(turns: List[dict]) -> str:
    users = [str(t.get("text_content") or "").strip() for t in turns if t.get("role") == "user"]
    pick = next((u for u in users if len(u.split()) >= 3), users[0] if users else "")
    text = mask_text(pick)
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS - 1].rstrip() + "…"


def _language(turns: List[dict]) -> Optional[str]:
    """The language most of the agent's replies were in ("hi", "en"...), None when the turns do not say."""
    seen: Dict[str, int] = {}
    for t in turns:
        code = str(t.get("language") or "").strip().lower()
        if code and t.get("role") == "agent":
            seen[code] = seen.get(code, 0) + 1
    return max(seen, key=lambda k: seen[k]) if seen else None


def _caller_of(conv: dict) -> Dict[str, Optional[str]]:
    """The name the caller gave and their number, masked, when the conversation kept them (local store)."""
    kept = conv.get("caller_details")
    kept = kept if isinstance(kept, dict) else {}
    name = " ".join(str(kept.get("name") or "").split())[:60]
    return {"name": mask_text(name) or None, "number": mask_number(kept.get("phone"))}


def build_call(conv: dict, turns: List[dict], agent_name: Optional[str]) -> dict:
    """One row of the list."""
    source = conv.get("source")
    channel = CHANNELS.get(source or "", "playground")
    when = conv.get("created_at") or (turns[0].get("created_at") if turns else None)
    who = _caller_of(conv)
    booked = _booked(turns)
    return {
        "id": conv["id"], "agent_id": conv.get("agent_id"), "agent_name": agent_name or "Deleted agent",
        "channel": channel, "called_back": source == "missed_call", "practice": bool(conv.get("is_test")), "when": when,
        "caller": who["number"], "caller_name": who["name"],
        "duration_s": _duration_s(conv, turns),
        "outcome": derive_outcome(source, bool(conv.get("needs_human")), turns),
        "booking": parse_booking(booked) if booked else None,
        "language": _language(turns),
        "replies": sum(1 for t in turns if t.get("role") == "agent"),
        "empty": not any(str(t.get("text_content") or "").strip() for t in turns),   # nothing was said by anyone
        "preview": _preview(turns),
    }


def build_transcript(turns: List[dict]) -> List[dict]:
    out = []
    for t in turns:
        item = {"role": "agent" if t.get("role") == "agent" else "caller", "text": mask_text(t.get("text_content")),
                "at": t.get("created_at"), "actions": []}
        for c in _tool_calls(t):  # what the agent did, never the arguments (they hold the caller's number)
            item["actions"].append({"name": c.get("name"), "ok": bool(c.get("ok")), "summary": mask_text(c.get("result"))[:200]})
        out.append(item)
    return out


# ------------------------------------------------------------------------------------------------------ loading

def _load_local(B, user_id: str, agent_id: Optional[str], since_iso: str, conv_id: Optional[str] = None,
                until_iso: Optional[str] = None):
    def keep(r: dict) -> bool:
        if r.get("user_id") != user_id:
            return False
        if conv_id is not None:
            return r.get("id") == conv_id
        made = str(r.get("created_at") or "")
        return (agent_id is None or r.get("agent_id") == agent_id) and made >= since_iso and (until_iso is None or made < until_iso)

    convs = B.local_db.select("conversations", keep, order="created_at", desc=True, limit=MAX_CANDIDATES)
    ids = {c["id"] for c in convs}
    turns: Dict[str, List[dict]] = {}
    for t in B.local_db.select("conversation_turns", lambda r: r.get("conversation_id") in ids, order="created_at"):
        turns.setdefault(t["conversation_id"], []).append(t)
    names = {a["id"]: a.get("name") for a in B.local_db.select("agents", lambda r: r.get("user_id") == user_id)}
    return convs, turns, names


def _load_cloud(B, user_id: str, agent_id: Optional[str], since_iso: str, conv_id: Optional[str] = None,
                until_iso: Optional[str] = None):
    """Supabase. Columns added by later migrations (010 is_test, 013 source, 016 needs_human) are asked
    for first and dropped one tier at a time when the database does not have them yet."""
    sb = B.supabase
    tiers = ("id, agent_id, started_at, source, is_test, needs_human",
             "id, agent_id, started_at, source, is_test", "id, agent_id, started_at")
    rows: List[dict] = []
    for n, cols in enumerate(tiers):
        try:
            q = sb.table("conversations").select(cols).eq("user_id", user_id)
            if conv_id is not None:
                q = q.eq("id", conv_id)
            else:
                q = q.gte("started_at", since_iso)
                if until_iso:
                    q = q.lt("started_at", until_iso)
                if agent_id:
                    q = q.eq("agent_id", agent_id)
            rows = q.order("started_at", desc=True).limit(MAX_CANDIDATES).execute().data or []
            break
        except Exception:  # noqa: BLE001
            if n == len(tiers) - 1:
                raise
    convs = [{**r, "created_at": r.get("started_at")} for r in rows]
    turns: Dict[str, List[dict]] = {}
    ids = [c["id"] for c in convs]
    tcols = ("conversation_id, role, text_content, created_at, fallback, total_ms, tool_calls, language",
             "conversation_id, role, text_content, created_at, fallback, total_ms, tool_calls",
             "conversation_id, role, text_content, created_at, fallback, total_ms",
             "conversation_id, role, text_content, created_at")
    for i in builtins.range(0, len(ids), 100):
        for n, cols in enumerate(tcols):
            try:
                got = (sb.table("conversation_turns").select(cols).in_("conversation_id", ids[i:i + 100])
                       .order("created_at").execute().data or [])
                break
            except Exception:  # noqa: BLE001
                if n == len(tcols) - 1:
                    raise
        for t in got:
            turns.setdefault(t["conversation_id"], []).append(t)
    agents = sb.table("agents").select("id, name").eq("user_id", user_id).execute().data or []
    return convs, turns, {a["id"]: a.get("name") for a in agents}


async def _load(request: Request, user_id: str, agent_id: Optional[str], since_iso: str, conv_id: Optional[str] = None,
                until_iso: Optional[str] = None):
    B = _b()

    if B.storage_mode_of(request) == "local":
        return await asyncio.to_thread(_load_local, B, user_id, agent_id, since_iso, conv_id, until_iso)
    return await B.cloud_run(lambda: _load_cloud(B, user_id, agent_id, since_iso, conv_id, until_iso), "load calls")


# ------------------------------------------------------------------------------------------------------ routes

RANGES = {"today": 0, "yesterday": 1, "7d": 6, "30d": 29}   # name -> days back from today (the first day of the window)
MAX_WINDOW_DAYS = 366


def timezone_name() -> str:
    """VAKYA_TIMEZONE (default Asia/Kolkata); a name the system does not know falls back to the default."""
    name = (os.environ.get("VAKYA_TIMEZONE") or "Asia/Kolkata").strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return "Asia/Kolkata"
    return name


def _day(value: str, label: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        raise AppError("INVALID_DATE", f"'{label}' must be a date like 2026-11-04.", 422, {"field": label})
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise AppError("INVALID_DATE", f"'{value}' is not a real date.", 422, {"field": label}) from None


def resolve_window(days: int, from_: Optional[str], to: Optional[str], range_: Optional[str], now: Optional[datetime] = None):
    """(first day, last day, start UTC ISO, end UTC ISO or None, number of days) of what the list covers. Days are whole
    calendar days in VAKYA_TIMEZONE with both ends included. Without from/to/range it is the last `days` days up to now."""
    zone = ZoneInfo(timezone_name())
    now = (now or datetime.now(timezone.utc)).astimezone(zone)
    today = now.date()
    if range_ is not None:
        if range_ not in RANGES:
            raise AppError("INVALID_RANGE", "range must be today, yesterday, 7d or 30d.", 422, {"field": "range"})
        first = today - timedelta(days=RANGES[range_])
        last = first if range_ in ("today", "yesterday") else today
    elif from_ is None and to is None:
        start = now - timedelta(days=days)
        return start.date(), today, start.astimezone(timezone.utc).isoformat(), None, days
    else:
        last = _day(to, "to") if to else today
        first = _day(from_, "from") if from_ else last - timedelta(days=29)
        if first > last:
            raise AppError("INVALID_RANGE", "The 'from' day is after the 'to' day.", 422, {"field": "from"})
        if (last - first).days + 1 > MAX_WINDOW_DAYS:
            raise AppError("INVALID_RANGE", f"Pick at most {MAX_WINDOW_DAYS} days at a time.", 422, {"field": "to"})
    start = datetime.combine(first, dtime.min, tzinfo=zone).astimezone(timezone.utc).isoformat()
    end = datetime.combine(last + timedelta(days=1), dtime.min, tzinfo=zone).astimezone(timezone.utc).isoformat()
    return first, last, start, end, (last - first).days + 1


@router.get("")
async def list_calls(request: Request, agent_id: Optional[str] = Query(None, max_length=64),
                     days: int = Query(30, ge=1, le=365), limit: int = Query(50, ge=1, le=200),
                     from_: Optional[str] = Query(None, alias="from", max_length=10),
                     to: Optional[str] = Query(None, max_length=10),
                     range_: Optional[str] = Query(None, alias="range", max_length=10),
                     hide_empty: bool = Query(False),
                     user_id: str = Depends(get_current_user_id)):
    """The owner's conversations, newest first: of the last `days` days, or of the days from..to (whole days in
    VAKYA_TIMEZONE, both included), or of a named `range`. An empty conversation is left out, except a phone call that
    connected and was never answered (that is an "Ended early" call); with `hide_empty` those go too and are counted."""
    first, last, since, until, span = resolve_window(days, from_, to, range_)
    convs, turns, names = await _load(request, user_id, agent_id, since, None, until)

    def assemble():
        out: List[dict] = []
        hidden = 0
        more = False
        for c in convs:
            t = turns.get(c["id"], [])
            if not t and c.get("source") not in PHONE_SOURCES:
                continue
            row = build_call(c, t, names.get(c.get("agent_id")))
            if hide_empty and row["empty"]:
                hidden += 1
                continue
            if len(out) >= limit:
                more = True
                continue
            out.append(row)
        return out, hidden, more

    calls, hidden, more = await asyncio.to_thread(assemble)
    return {"calls": calls, "days": span, "from": first.isoformat(), "to": last.isoformat(), "timezone": timezone_name(),
            "empty_hidden": hidden, "truncated": more}


@router.get("/{conversation_id}")
async def get_call(conversation_id: str, request: Request, user_id: str = Depends(get_current_user_id)):
    """One conversation (the owner's only) with its transcript."""
    convs, turns, names = await _load(request, user_id, None, "", conversation_id)
    conv = next((c for c in convs if c["id"] == conversation_id), None)
    if conv is None:
        raise AppError("NOT_FOUND", "Conversation not found", 404)
    t = turns.get(conversation_id, [])
    call = await asyncio.to_thread(build_call, conv, t, names.get(conv.get("agent_id")))
    B = _b()
    details = await asyncio.to_thread(caller_details, B, request, conversation_id, user_id)
    return {**call, "transcript": build_transcript(t), "details": details}
