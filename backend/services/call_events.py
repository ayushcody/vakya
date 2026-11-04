"""The call event log: what a live call is doing, as a small stream of structured events.

Every spoken turn (the browser web call, the owner's own turns and the phone call: they all run `turn_event_stream` or
`turn_blocking`) appends events here as it goes: what the caller said and in which language, each scheduling tool call and
its result, each sentence the agent speaks, a booking being made, and what the desk is listening for and which learnt correction ran
(`hearing`: kind "listening" or "corrected", with a one-line `label`; services/hearing.py). The desk's live screen (components/live/) and the phone
monitor subscribe to them: nothing is polled from the database and nothing here is stored for good.

The log is in memory (a ring of the latest events per front desk), thread safe (tools run in worker threads) and bounded.
An event is a dict with an envelope and a type-specific body; the schema is in docs/ARCHITECTURE.md ("Live call events").

    emit(agent_id, conversation_id, turn_id, "tool", **body) -> the event (with its `seq`)
    since(agent_id, after=0, conversation_id=None, limit=200) -> events with seq > after, oldest first
    wait_for(agent_id, after, timeout, conversation_id=None) -> like since, but waits (blocking) for a new event
    subscribe(agent_id, callback) -> an unsubscribe function; the callback runs on the emitting thread, keep it short

Privacy: a phone number is never in an event (only its last four digits), and the log is owner-read only
(routers/turns.py checks the front desk belongs to the signed-in user)."""
from __future__ import annotations

import collections
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

RING = 600                    # events kept per front desk
MAX_AGENTS = 64               # front desks kept (the least recently used one goes)
EVENT_TYPES = ("caller", "tool", "agent", "booking", "turn_end", "hearing")

_lock = threading.RLock()
_cond = threading.Condition(_lock)
_rings: "collections.OrderedDict[str, Deque[dict]]" = collections.OrderedDict()
_subs: Dict[str, List[Callable[[dict], None]]] = {}
_seq = 0


def enabled() -> bool:
    import os  # noqa: PLC0415
    return os.getenv("CALL_EVENTS", "on").strip().lower() not in ("off", "0", "false", "no")


def reset() -> None:
    """Forget everything (tests)."""
    global _seq
    with _lock:
        _rings.clear()
        _subs.clear()
        _seq = 0


# ------------------------------------------------------------------------------------------------ the log

def emit(agent_id: Optional[str], conversation_id: Optional[str], turn_id: Optional[str], type_: str,
         **body: Any) -> Optional[dict]:
    """Append one event and wake the waiting readers. Never raises: a log that fails must not break a call."""
    if not agent_id or not enabled():
        return None
    global _seq
    try:
        with _cond:
            _seq += 1
            event = {"seq": _seq, "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                     "type": type_, "agent_id": agent_id, "conversation_id": conversation_id, "turn_id": turn_id, **body}
            ring = _rings.get(agent_id)
            if ring is None:
                ring = _rings[agent_id] = collections.deque(maxlen=RING)
            ring.append(event)
            _rings.move_to_end(agent_id)
            while len(_rings) > MAX_AGENTS:
                _rings.popitem(last=False)
            listeners = list(_subs.get(agent_id, ()))
            _cond.notify_all()
        for fn in listeners:
            try:
                fn(event)
            except Exception:  # noqa: BLE001 - a listener must not break the call
                pass
        return event
    except Exception:  # noqa: BLE001
        return None


def since(agent_id: str, after: int = 0, conversation_id: Optional[str] = None, limit: int = 200) -> List[dict]:
    with _lock:
        ring = _rings.get(agent_id)
        rows = [e for e in (ring or ()) if e["seq"] > after and (conversation_id is None or e["conversation_id"] == conversation_id)]
    return rows[:limit]


def last_seq() -> int:
    with _lock:
        return _seq


def wait_for(agent_id: str, after: int, timeout: float, conversation_id: Optional[str] = None) -> List[dict]:
    """Blocking: the events after `after`, waiting up to `timeout` seconds when there are none yet."""
    deadline = time.monotonic() + timeout
    with _cond:
        while True:
            rows = since(agent_id, after, conversation_id)
            if rows:
                return rows
            left = deadline - time.monotonic()
            if left <= 0:
                return []
            _cond.wait(min(left, 1.0))


def subscribe(agent_id: str, callback: Callable[[dict], None]) -> Callable[[], None]:
    with _lock:
        _subs.setdefault(agent_id, []).append(callback)

    def off() -> None:
        with _lock:
            fns = _subs.get(agent_id) or []
            if callback in fns:
                fns.remove(callback)
    return off


# ------------------------------------------------------------------------------------------------ language and script

# Words that settle Hindi against Marathi in Devanagari (both are written in it). A word of neither list takes the
# utterance's label; when an utterance has markers of neither language (or of both, evenly) the label is "unsure".
_MR = {"आहे", "आहेत", "नाही", "नाहीत", "काय", "मला", "तुम्ही", "आम्ही", "हवे", "हवी", "हवा", "पाहिजे", "आणि", "माझे", "माझी",
       "माझा", "तुमचे", "तुमची", "तुमचा", "मी", "करायचे", "करायची", "आहात", "होय", "कधी", "कुठे", "किती", "द्या", "बुकिंग्ज",
       "सांगा", "सकाळी", "संध्याकाळी", "उद्या", "परवा", "वाजता", "वाजले", "लागेल", "होईल", "का", "तर", "पण", "आज"}
_HI = {"है", "हैं", "हूँ", "हूं", "नहीं", "क्या", "मुझे", "आप", "और", "मैं", "मेरा", "मेरी", "मेरे", "चाहिए", "कीजिए",
       "कर", "करना", "करनी", "कल", "कब", "कहाँ", "कहां", "कितना", "कितने", "दीजिए", "बताइए", "सुबह", "शाम", "बजे", "लिए",
       "हमारा", "आपका", "आपकी", "हाँ", "हां", "जी", "था", "थी", "रहा", "रही", "गया", "सकता", "सकती", "होगा", "होगी"}
# Words shared by both that must not count as evidence ("का", "आज", "तर" are in both lists above only where listed once).
_SHARED = {"का", "आज", "तर", "पण"}
_MR -= {"का", "आज", "तर", "पण"}

_WORD = re.compile(r"[\wऀ-ॿ]+(?:['’\-][\wऀ-ॿ]+)*", re.UNICODE)


def _norm(word: str) -> str:
    return unicodedata.normalize("NFC", word).strip("।.,?!;:\"'()")


def script_of_word(word: str) -> str:
    """"deva" (Devanagari), "latn" (Latin), "num" (digits only) or "other"."""
    deva = latin = 0
    for ch in word:
        if "ऀ" <= ch <= "ॿ":
            deva += 1
        elif ch.isalpha() and ch.isascii():
            latin += 1
        elif ch.isalpha():
            return "other"
    if deva and deva >= latin:
        return "deva"
    if latin:
        return "latn"
    return "num" if any(c.isdigit() for c in word) else "other"


def devanagari_language(text: str) -> Tuple[str, bool]:
    """("hi" or "mr", sure) for Devanagari text: Marathi and Hindi look alike, so only their own function words tell them
    apart. Not sure when neither has a marker or they tie (the label then falls back to Hindi, which callers of an Indian
    front desk speak more often, and says so)."""
    mr = hi = 0
    for w in _WORD.findall(text or ""):
        w = _norm(w)
        if w in _MR:
            mr += 1
        elif w in _HI:
            hi += 1
    if mr == hi:
        return "hi", False
    return ("mr", True) if mr > hi else ("hi", True)


def script_label(text: str) -> str:
    """"latin", "devanagari", "mixed" (both, as in Hinglish typed in two scripts), "other" or "none"."""
    deva = latin = other = 0
    for w in _WORD.findall(text or ""):
        s = script_of_word(w)
        deva += s == "deva"
        latin += s == "latn"
        other += s == "other"
    if not (deva or latin or other):
        return "none"
    if other and not (deva or latin):
        return "other"
    if deva and latin:
        return "mixed"
    return "devanagari" if deva else "latin"


def tag_words(text: str, language: Optional[str] = None) -> List[List[str]]:
    """[[word, lang], ...] for the screen's tinting: "en" for Latin words, "hi" or "mr" for Devanagari words, "x" when the
    language of a word cannot be told (a number, another script, Devanagari of an unsure utterance). A Devanagari word
    that is a Hindi-only or Marathi-only function word takes that language; any other Devanagari word takes the
    utterance's label (`language` "hi"/"mr" when it is sure of it, else "x")."""
    out: List[List[str]] = []
    for w in str(text or "").split():
        core = _norm(w)
        s = script_of_word(core) if core else "other"
        if s == "latn":
            lang = "en"
        elif s == "deva":
            lang = "mr" if core in _MR else "hi" if core in _HI else (language if language in ("hi", "mr") else "x")
        else:
            lang = "x"
        out.append([w, lang])
    return out


def utterance_language(text: str, detected: Optional[str], confidence: Optional[str] = None) -> dict:
    """What the screen says about an utterance's language: {language, script, unsure, words}. `detected` is the turn's
    language code (services/language_policy.py). The tint is only claimed when it is earned: Devanagari with neither a
    Hindi nor a Marathi marker, or a low-confidence recognition, is "unsure"."""
    script = script_label(text)
    language = detected or "en"
    unsure = confidence == "low"
    word_lang: Optional[str] = None
    if script in ("devanagari", "mixed"):
        label, sure = devanagari_language(text)
        if language in ("hi", "mr"):
            language = label            # the function words decide between the two; none of them: Hindi, flagged
            unsure = unsure or not sure
        if sure:
            word_lang = label
    return {"language": language, "script": script, "unsure": unsure, "words": tag_words(text, word_lang)}


# ------------------------------------------------------------------------------------------------ tool events

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _day(date_iso: Optional[str]) -> str:
    try:
        d = datetime.strptime(str(date_iso), "%Y-%m-%d")
    except ValueError:
        return str(date_iso or "")
    return f"{_WEEKDAYS[d.weekday()]} {d.day} {d.strftime('%b')}"


def _clock(hhmm: Optional[str]) -> str:
    try:
        t = datetime.strptime(str(hhmm), "%H:%M")
    except ValueError:
        return str(hhmm or "")
    hour = t.hour % 12 or 12
    suffix = "am" if t.hour < 12 else "pm"
    return f"{hour}:{t.minute:02d} {suffix}" if t.minute else f"{hour} {suffix}"


def when_words(date_iso: Optional[str], time_hhmm: Optional[str], *, with_date: bool = True) -> str:
    return " ".join(x for x in ((_day(date_iso) if with_date else ""), _clock(time_hhmm) if time_hhmm else "") if x)


def redact_ids(text: str) -> str:
    """Identity numbers (Aadhaar, PAN, card, OTP) replaced by "[ID number removed]" (services/id_redact.py)."""
    try:
        from services import id_redact  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.services import id_redact  # noqa: PLC0415
    return id_redact.redact_ids(text)


_LONG_NUMBER = re.compile(r"(?<!\d)\+?\d(?:[\s\-]?\d){6,}(?!\d)")


def mask_numbers(text: str) -> str:
    """A spoken phone number in a transcript shown on the desk keeps only its last four digits: "98765 43210" -> "••••3210"."""
    def sub(m: "re.Match") -> str:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", m.group(0)):    # a date is not a number to hide
            return m.group(0)
        return "••••" + re.sub(r"\D", "", m.group(0))[-4:]
    return _LONG_NUMBER.sub(sub, redact_ids(str(text or "")))


def mask_phone(value: Any) -> Optional[str]:
    digits = re.sub(r"\D", "", str(value or ""))
    return (f"••••{digits[-4:]}" if len(digits) >= 5 else "••••") if digits else None


def _option(o: Optional[dict]) -> Optional[dict]:
    if not isinstance(o, dict) or not o.get("date"):
        return None
    return {"date": o["date"], **({"time": o["time"]} if o.get("time") else {})}


def tool_event_body(name: str, args: dict, ok: Optional[bool], value: Any, *, schedule_id: Optional[str] = None,
                    schedule_name: Optional[str] = None, phase: str = "end") -> dict:
    """The body of a `tool` event (see docs/ARCHITECTURE.md). `phase` "start" has no result yet (`ok` None).

    status: checking | free | taken | listing | listed | readback | booking | booked | refused | confirm | cancelling |
    cancelled | finding | found | failed. `label` is one line to show as it is."""
    args = args if isinstance(args, dict) else {}
    body: Dict[str, Any] = {"tool": name, "phase": phase, "schedule_id": schedule_id,
                            "schedule": (value.get("schedule") if isinstance(value, dict) else None) or schedule_name}
    date_iso, time_hhmm = args.get("date"), args.get("time")
    start = phase == "start"

    if name == "check_availability":
        body.update(date=date_iso, time=time_hhmm)
        if start:
            body.update(status="checking", label=f"Checking {when_words(date_iso, time_hhmm)}".strip())
        elif ok and isinstance(value, dict):
            avail = bool(value.get("available"))
            options = [x for x in (_option(value.get("before")), _option(value.get("after"))) if x]
            body.update(status="free" if avail else "taken", available=avail, reason=value.get("reason"),
                        date=value.get("date") or date_iso, time=value.get("time") or time_hhmm, options=options,
                        before=_option(value.get("before")), after=_option(value.get("after")))
            head = f"Checked {when_words(body['date'], body['time'])}: "
            if avail:
                body["label"] = head + "free"
            else:
                offered = [when_words(o["date"], o.get("time"), with_date=o["date"] != body["date"]) for o in options]
                why = {"closed": "closed", "past": "already past", "outside_hours": "outside opening hours",
                       "too_soon": "too soon to book", "too_far": "too far ahead", "blocked": "busy"}.get(
                    str(value.get("reason")), "taken")
                body["label"] = head + why + (", offered " + " or ".join(offered) if offered else "")
        else:
            body.update(status="failed", label="Could not check that time")
    elif name == "list_free_slots":
        body.update(date=date_iso)
        if start:
            body.update(status="listing", label=f"Looking at {_day(date_iso)}")
        elif ok and isinstance(value, dict):
            free = [str(x) for x in (value.get("free") or []) if isinstance(x, str)][:16]
            body.update(status="listed", free=free, count=value.get("count", len(free)),
                        label=f"Listed {_day(value.get('date') or date_iso)}: " + (f"{len(free)} free" if free else "nothing free"))
        else:
            body.update(status="failed", label="Could not list the free times")
    elif name == "book":
        body.update(date=date_iso, time=time_hhmm, name_given=bool(args.get("name")), phone=mask_phone(args.get("phone")))
        if start:
            body.update(status="booking", label=f"Booking {when_words(date_iso, time_hhmm)}")
        elif ok and isinstance(value, dict) and value.get("booking_id"):
            body.update(status="booked", booking_id=value["booking_id"], date=value.get("date") or date_iso,
                        time=value.get("time") or time_hhmm,
                        label=f"Booked {value['booking_id']}: {when_words(value.get('date') or date_iso, value.get('time') or time_hhmm)}")
        elif isinstance(value, str) and value.startswith("NOT DONE: first read this back"):
            body.update(status="readback", label=f"Reading back {when_words(date_iso, time_hhmm)}, waiting for a yes")
        elif isinstance(value, dict) and value.get("booked") is False:
            body.update(status="refused", reason=value.get("reason"),
                        options=[x for x in (_option(o) for o in value.get("alternatives") or []) if x],
                        label=f"Could not book {when_words(date_iso, time_hhmm)}")
        else:
            body.update(status="failed", label="Could not book that")
    elif name == "cancel":
        body.update(booking_id=str(args.get("booking_id") or "").upper() or None)
        if start:
            body.update(status="cancelling", label=f"Cancelling {body['booking_id'] or 'a booking'}")
        elif ok:
            body.update(status="cancelled", label=f"Cancelled {body['booking_id']}")
        elif isinstance(value, str) and value.startswith("NOT DONE: the caller has not confirmed"):
            body.update(status="confirm", label=f"Asking to confirm cancelling {body['booking_id']}")
        else:
            body.update(status="failed", label="Could not cancel that")
    elif name == "find_booking":
        body.update(phone=mask_phone(args.get("phone")))
        if start:
            body.update(status="finding", label="Looking up the caller's bookings")
        elif ok and isinstance(value, dict):
            rows = [b for b in value.get("bookings") or [] if isinstance(b, dict)]
            body.update(status="found", count=len(rows),
                        bookings=[{"booking_id": b.get("booking_id"), "date": b.get("date"), "time": b.get("time")} for b in rows[:5]],
                        label=f"Found {len(rows)} upcoming booking{'' if len(rows) == 1 else 's'}")
        else:
            body.update(status="failed", label="Could not look up bookings")
    else:
        body.update(status="checking" if start else ("free" if ok else "failed"), label=f"{name}")
    return body
