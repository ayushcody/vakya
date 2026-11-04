"""Running late and the token queue: "How many patients before me? Has the doctor come? Is he late?"

A time-slot schedule can say, for today only, that it is running late:

    {minutes_late 0..480, reason (optional, at most 80 characters), set_at, now_serving_token (optional)}

kept in one small JSON file next to the schedules (default backend/data/delays.json, VAKYA_DELAYS_FILE overrides; with the
schedules file moved, it moves with it), written atomically under the same kind of lock as services/schedules.py. An entry
belongs to a date and is ignored after that day: it clears itself at midnight (and is dropped from the file by the next
write). Nothing about a patient is stored here.

Tokens are not stored. A booking's token for the day is its place among that day's live bookings of the schedule ordered
by start (1-based), so a cancellation renumbers the rest the same way for the desk and for the phone
(services/schedule_bookings.token_map). The estimate for a booking is

    expected_at = max(slot start + minutes_late, now + people_ahead x slot interval)    when now_serving_token is set
    expected_at = slot start + minutes_late                                            otherwise

rounded to 5 minutes. `people_ahead` counts the patient in with the doctor (token == now serving) as one: they leave in about
one slot. Without a now-serving token it is the earlier tokens whose slot (moved by the delay) has not finished yet.

The agent reads the queue with the `queue_status` tool (services/schedule_tools.py) and the prompt carries a one-line note
while a delay is set (`prompt_block`). The desk reads and sets it through routers/delays.py; "Call to let them know" places an
outbound call whose first words are the notice built here (`build_notice`), kept in memory for NOTICE_TTL_S and matched to the
call by the number dialled."""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:
    from services import availability as av, caller_profiles, schedule_bookings as sb, schedules as sch
except ImportError:  # pragma: no cover
    from backend.services import availability as av, caller_profiles, schedule_bookings as sb, schedules as sch

MAX_LATE = 480
REASON_MAX = 80
AFFECTED_MIN = 15          # a booking is listed for a call when its expected time moved by at least this many minutes
ROUND_TO = 5
NOTICE_TTL_S = 20 * 60
LANGS = ("en", "hi", "mr")

_thread_lock = threading.RLock()


# ---------------------------------------------------------------------------------------------- storage

def data_path() -> str:
    return os.getenv("VAKYA_DELAYS_FILE") or os.path.join(os.path.dirname(sch.data_path()), "delays.json")


def _read(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("delays"), dict):
            return data
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        print(f"delays: {path} is unreadable ({e}); starting a new file", file=sys.stderr)
        with contextlib.suppress(OSError):
            os.replace(path, path + ".corrupt")
    return {"delays": {}}


def _write(path: str, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


@contextlib.contextmanager
def _transaction(today: str):
    """The data under a lock shared by threads and processes, saved when the block ends without error. Entries of earlier
    days are dropped first."""
    path = data_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _thread_lock:
        lock_file = open(path + ".lock", "a")
        try:
            if fcntl:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
            data = _read(path)
            before = json.dumps(data, sort_keys=True)
            data["delays"] = {k: v for k, v in data["delays"].items() if isinstance(v, dict) and v.get("date") == today}
            yield data
            if json.dumps(data, sort_keys=True) != before:
                _write(path, data)
        finally:
            lock_file.close()


def _today(schedule: dict, now: Optional[datetime]) -> Tuple[datetime, str]:
    current = now or sch.now(schedule.get("timezone"))
    return current, current.date().isoformat()


def state_of(schedule: dict, now: Optional[datetime] = None) -> Optional[dict]:
    """Today's running-late state of a schedule, or None when nothing is set (or it was set on an earlier day)."""
    _current, today = _today(schedule, now)
    entry = _read(data_path())["delays"].get(schedule["id"])
    if not isinstance(entry, dict) or entry.get("date") != today:
        return None
    return {"minutes_late": int(entry.get("minutes_late") or 0), "reason": entry.get("reason") or None,
            "set_at": entry.get("set_at"), "now_serving_token": entry.get("now_serving_token")}


def _need_slots(schedule: dict) -> None:
    if schedule.get("kind") == "nights":
        raise sch.ScheduleError("NOT_TIME_SLOTS", "Running late and tokens are for time-slot schedules, not stays.")


def set_delay(schedule: dict, minutes_late: Any, reason: Any = None, now: Optional[datetime] = None) -> Optional[dict]:
    """Say the schedule is running `minutes_late` minutes late today (0 clears it). Raises ScheduleError."""
    _need_slots(schedule)
    if isinstance(minutes_late, bool) or not isinstance(minutes_late, int) or not 0 <= minutes_late <= MAX_LATE:
        raise sch.ScheduleError("INVALID_MINUTES", f"minutes_late must be a whole number from 0 to {MAX_LATE}.",
                                "minutes_late")
    text = re.sub(r"\s+", " ", str(reason or "")).strip()
    if len(text) > REASON_MAX:
        raise sch.ScheduleError("INVALID_REASON", f"The reason can be at most {REASON_MAX} characters.", "reason")
    current, today = _today(schedule, now)
    with _transaction(today) as data:
        entry = data["delays"].get(schedule["id"]) or {"date": today, "now_serving_token": None}
        entry.update({"date": today, "minutes_late": minutes_late, "reason": text if minutes_late else "",
                      "set_at": current.isoformat(timespec="seconds")})
        if minutes_late == 0 and not entry.get("now_serving_token"):
            data["delays"].pop(schedule["id"], None)
        else:
            data["delays"][schedule["id"]] = entry
    return state_of(schedule, current)


def set_now_serving(schedule: dict, token: Any, last_token: int, now: Optional[datetime] = None) -> Optional[dict]:
    """Say which token is with the doctor: a number from 0 (nobody yet) to `last_token`, or "next". Raises ScheduleError."""
    _need_slots(schedule)
    current, today = _today(schedule, now)
    with _transaction(today) as data:
        entry = data["delays"].get(schedule["id"]) or {"date": today, "minutes_late": 0, "reason": "", "set_at": None,
                                                       "now_serving_token": None}
        if isinstance(token, str) and token.strip().lower() == "next":
            wanted = int(entry.get("now_serving_token") or 0) + 1
            if wanted > last_token:
                raise sch.ScheduleError("NO_MORE_TOKENS", "That was the last token of the day.", "token")
        elif isinstance(token, bool) or not isinstance(token, int) or not 0 <= token <= max(last_token, 0):
            raise sch.ScheduleError("INVALID_TOKEN", f"token must be \"next\" or a number from 0 to {last_token}.", "token")
        else:
            wanted = token
        entry["date"] = today
        entry["now_serving_token"] = wanted or None
        entry["set_at"] = entry.get("set_at") or current.isoformat(timespec="seconds")
        if not entry.get("minutes_late") and not entry["now_serving_token"]:
            data["delays"].pop(schedule["id"], None)
        else:
            data["delays"][schedule["id"]] = entry
    return state_of(schedule, current)


# ---------------------------------------------------------------------------------------------- plain words

_MR_PERIODS = (("सकाळी", 4, 12), ("दुपारी", 12, 16), ("संध्याकाळी", 16, 20))


def _period(hour: int, lang: str) -> str:
    if lang == "hi":
        return av.period_hi(hour)
    for word, a, b in _MR_PERIODS:
        if a <= hour < b:
            return word
    return "रात्री"


def clock(dt: datetime, lang: str) -> str:
    """"6:40 pm", "शाम 6:40", "संध्याकाळी 6:40": the helpers of services/availability.py and caller_profiles.py."""
    if lang == "en":
        return av.time_en(dt)
    return f"{_period(dt.hour, lang)} {caller_profiles._time_phrase(dt.strftime('%H:%M'), lang)}"   # noqa: SLF001


def about(dt: datetime, lang: str) -> str:
    """"about 6:40 pm", "लगभग शाम 6:40", "सुमारे संध्याकाळी 6:40"."""
    return {"en": "about ", "hi": "लगभग ", "mr": "सुमारे "}[lang] + clock(dt, lang)


_DOCTOR = re.compile(r"doctor|\bdr\b|consult|clinic|opd|dent|physio|डॉक्टर|डाक्टर|परामर्श|दवाखान|दंत", re.I)


def is_doctor(schedule: dict) -> bool:
    return bool(_DOCTOR.search(str(schedule.get("name") or "")))


def late_line(schedule: dict, minutes: int) -> Dict[str, str]:
    """"The doctor is running about 40 minutes late today." in the three languages."""
    if is_doctor(schedule):
        return {"en": f"The doctor is running about {minutes} minutes late today.",
                "hi": f"आज डॉक्टर लगभग {minutes} मिनट देरी से चल रहे हैं।",
                "mr": f"आज डॉक्टर सुमारे {minutes} मिनिटे उशिरा चालले आहेत."}
    return {"en": f"We are running about {minutes} minutes late today.",
            "hi": f"आज हम लगभग {minutes} मिनट देरी से चल रहे हैं।",
            "mr": f"आज आम्ही सुमारे {minutes} मिनिटे उशिरा चालत आहोत."}


# ---------------------------------------------------------------------------------------------- the queue

def _round(dt: datetime) -> datetime:
    total = dt.hour * 60 + dt.minute + (1 if dt.second >= 30 else 0)
    total = int(round(total / ROUND_TO)) * ROUND_TO
    return dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=total)


def _order(rows: List[dict]) -> List[dict]:
    return sorted(rows, key=lambda b: (b["start"], sch._id_num(b.get("id") or "")))   # noqa: SLF001


def queue(schedule: dict, rows: Optional[List[dict]] = None, now: Optional[datetime] = None) -> dict:
    """Today's queue of a time-slot schedule: tokens with expected times and status, and the bookings that moved by
    AFFECTED_MIN or more (`affected`). Pure of the clock (`now`) and the stored rows (`rows`)."""
    _need_slots(schedule)
    current, today = _today(schedule, now)
    state = state_of(schedule, current) or {"minutes_late": 0, "reason": None, "set_at": None, "now_serving_token": None}
    late = state["minutes_late"]
    serving = state["now_serving_token"]
    interval = int(schedule["slot_minutes"]) + int(schedule.get("buffer_minutes") or 0)
    stored = sb._ap().all_bookings() if rows is None else rows   # noqa: SLF001
    day = [b for b in av.live_bookings(stored, schedule["id"])
           if not b.get("nights") and b["start"].date() == current.date()]
    stored_by_id = {r.get("booking_id"): r for r in stored}
    ordered = _order(day)
    out: List[dict] = []
    for i, b in enumerate(ordered, start=1):
        start = b["start"]
        slot_end = start + timedelta(minutes=interval)
        moved = start + timedelta(minutes=late)
        if serving:
            status = "seen" if i < serving else "now" if i == serving else "waiting"
            ahead = max(0, i - serving)
        else:
            status = "seen" if moved + timedelta(minutes=interval) <= current else \
                "now" if moved <= current else "waiting"
            ahead = sum(1 for earlier in out if earlier["status"] in ("waiting", "now"))
        expected = moved
        if serving and status == "waiting":
            expected = max(moved, current + timedelta(minutes=ahead * interval))
        expected = _round(expected)
        shift = int((expected - start).total_seconds() // 60)
        original = stored_by_id.get(b["id"]) or {}
        out.append({"booking_id": b["id"], "token": i, "name": b["name"], "phone_masked": sb._mask(b.get("phone")),   # noqa: SLF001
                    "source": b.get("source") or "phone", "slot_start": start.strftime("%H:%M"),
                    "slot_end": slot_end.strftime("%H:%M"), "expected_at": expected.strftime("%H:%M"),
                    "expected_say": {lg: about(expected, lg) for lg in LANGS}, "status": status,
                    "people_ahead": ahead, "moved_minutes": max(0, shift), "can_call": bool(b.get("phone")),
                    **({"booked_by": original["booked_by"]} if original.get("booked_by") else {}),
                    **({"relation": original["relation"]} if original.get("relation") else {})})
    affected = [{**t, "suggested_time": t["expected_at"]} for t in out
                if t["status"] == "waiting" and t["moved_minutes"] >= AFFECTED_MIN]
    return {"schedule_id": schedule["id"], "schedule_name": schedule["name"], "date": today, "slot_minutes": interval,
            "minutes_late": late, "reason": state["reason"], "set_at": state["set_at"], "now_serving": serving,
            "late_say": late_line(schedule, late) if late else None, "count": len(out), "tokens": out,
            "affected": affected}


# ---------------------------------------------------------------------------------------------- the phone

def _digits(value: Any) -> str:
    d = re.sub(r"\D", "", str(value or ""))
    return d[-10:] if len(d) >= 10 else d


def _name_key(value: Any) -> str:
    return re.sub(r"[^\w]+", " ", str(value or "").casefold()).strip()


def queue_status(agent_id: str, name: Any = None, phone: Any = None, schedule_name: Any = None,
                 now: Optional[datetime] = None) -> Tuple[bool, Any]:
    """The `queue_status` tool: where the caller stands today. (ok, value); a failure's value is a sentence."""
    digits = sb.clean_phone(phone, required=False) if phone else ""
    wanted = _name_key(name)
    if not digits and not wanted:
        return False, "Ask for the caller's name or phone number first."
    try:
        from services import schedule_tools as st  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.services import schedule_tools as st  # noqa: PLC0415
    rows = [s for s in sch.list_schedules(agent_id) if s.get("kind") != "nights"]
    if not rows:
        return False, "This front desk has no time-slot schedule, so there is no queue."
    if schedule_name:
        chosen, problem = st.resolve_schedule(agent_id, schedule_name)
        if chosen is None:
            return False, problem
        rows = [chosen] if chosen.get("kind") != "nights" else rows
    stored = sb._ap().all_bookings()   # noqa: SLF001
    hits: List[Tuple[dict, dict, dict]] = []
    for s in rows:
        q = queue(s, stored, now)
        for t in q["tokens"]:
            b = next((r for r in stored if r.get("booking_id") == t["booking_id"]), {})
            if digits and _digits(b.get("phone")) == digits:
                hits.append((s, q, t))
            elif not digits and wanted and (_name_key(t["name"]) == wanted or wanted in _name_key(t["name"]).split()
                                            or _name_key(t["name"]) in wanted.split()):
                hits.append((s, q, t))
    if not digits:
        people = {_digits(next((r for r in stored if r.get("booking_id") == t["booking_id"]), {}).get("phone")) or t["booking_id"]
                  for _s, _q, t in hits}
        if len(people) > 1:
            return False, "More than one booking today has that name. Ask for the phone number the booking was made with."
    first = rows[0]
    if not hits:
        q = queue(first, stored, now) if len(rows) == 1 else None
        out: Dict[str, Any] = {"found": False, "date": (now or sch.now(first.get("timezone"))).date().isoformat()}
        delayed = [(s, state_of(s, now)) for s in rows]
        delayed = [(s, d) for s, d in delayed if d and d["minutes_late"]]
        if delayed:
            s0, d0 = delayed[0]
            out.update({"minutes_late": d0["minutes_late"], "reason": d0["reason"], "late_say": late_line(s0, d0["minutes_late"])})
        out["say"] = {"en": "I cannot find a booking for today with that name or number. Ask the caller to check the number "
                            "they booked with, or offer to book.",
                      "hi": "आज इस नाम या नंबर से कोई बुकिंग नहीं मिली। कॉल करने वाले से बुकिंग वाला नंबर पूछें, या बुकिंग करने की पेशकश करें।",
                      "mr": "आज या नावाने किंवा नंबरने बुकिंग सापडले नाही. बुकिंग केलेला नंबर विचारा किंवा बुकिंग करण्याची ऑफर द्या."}
        if q:
            out["now_serving"] = q["now_serving"]
        return True, out
    s, q, t = hits[0]
    out = {"found": True, "schedule": s["name"], "booking_id": t["booking_id"], "name": t["name"], "token": t["token"],
           "slot_time": t["slot_start"], "status": t["status"], "people_ahead": t["people_ahead"],
           "expected_at": t["expected_at"], "minutes_late": q["minutes_late"], "reason": q["reason"],
           "now_serving": q["now_serving"], "tokens_today": q["count"]}
    if len(hits) > 1:
        out["other_bookings_today"] = [{"schedule": hs["name"], "token": ht["token"], "slot_time": ht["slot_start"]}
                                       for hs, _hq, ht in hits[1:]]
    out["say"] = {lg: _status_line(s, q, t, lg) for lg in LANGS}
    return True, out


def _hhmm_dt(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}")


def _status_line(schedule: dict, q: dict, t: dict, lang: str) -> str:
    slot = _hhmm_dt(q["date"], t["slot_start"])
    expected = _hhmm_dt(q["date"], t["expected_at"])
    n, ahead, late = t["token"], t["people_ahead"], q["minutes_late"]
    delayed = late and t["moved_minutes"] >= 5
    if t["status"] == "seen":
        return {"en": f"You are token {n}, and your turn has already passed. If you have not been seen, please tell the desk.",
                "hi": f"आपका टोकन {n} है, और आपकी बारी निकल चुकी है। अगर आपको नहीं देखा गया है तो कृपया डेस्क को बताइए।",
                "mr": f"तुमचा टोकन {n} आहे, आणि तुमची वेळ होऊन गेली आहे. डॉक्टरांनी अजून तुम्हाला पाहिले नसेल तर कृपया डेस्कला सांगा."}[lang]
    if t["status"] == "now":
        return {"en": f"You are token {n}, and it is your turn now.",
                "hi": f"आपका टोकन {n} है, और अभी आपकी बारी है।",
                "mr": f"तुमचा टोकन {n} आहे, आणि आता तुमची बारी आहे."}[lang]
    if ahead == 0:
        count = {"en": "nobody is ahead of you", "hi": "आपसे पहले कोई नहीं है", "mr": "तुमच्या आधी कोणी नाही"}[lang]
    elif ahead == 1:
        count = {"en": "1 patient is ahead of you", "hi": "आपसे पहले 1 मरीज़ है", "mr": "तुमच्या आधी 1 रुग्ण आहे"}[lang]
    else:
        count = {"en": f"{ahead} patients are ahead of you", "hi": f"आपसे पहले {ahead} मरीज़ हैं",
                 "mr": f"तुमच्या आधी {ahead} रुग्ण आहेत"}[lang]
    if delayed:
        subject = late_line(schedule, late)[lang].rstrip("।.")
        return {"en": f"You are token {n}; {count}. {subject}, so please come by {about(expected, 'en')}.",
                "hi": f"आपका टोकन {n} है; {count}। {subject}, इसलिए कृपया {about(expected, 'hi')} तक आइए।",
                "mr": f"तुमचा टोकन {n} आहे; {count}. {subject}, म्हणून कृपया {about(expected, 'mr')} पर्यंत या."}[lang]
    return {"en": f"You are token {n}; {count}. Your time is {clock(slot, 'en')}.",
            "hi": f"आपका टोकन {n} है; {count}। आपका समय {clock(slot, 'hi')} है।",
            "mr": f"तुमचा टोकन {n} आहे; {count}. तुमची वेळ {clock(slot, 'mr')} आहे."}[lang]


def running_late_note(schedule: dict, now: Optional[datetime] = None) -> Optional[dict]:
    """{"minutes_late", "reason", "expected_at"-free summary} for a booking made today: what `book` adds to its result."""
    state = state_of(schedule, now)
    if not state or not state["minutes_late"]:
        return None
    return {"minutes_late": state["minutes_late"], "reason": state["reason"], "say": late_line(schedule, state["minutes_late"]),
            "next_step": "Tell the caller the delay before they hang up, and that they may come a little later."}


def prompt_block(agent_id: Optional[str], conversation_id: Optional[str] = None, now: Optional[datetime] = None) -> str:
    """The note for a turn's prompt: today's delay on this front desk's schedules, and (on a call the desk placed to tell
    someone) what the call is for. "" when there is nothing; never raises."""
    try:
        parts: List[str] = []
        if agent_id:
            lines = []
            for s in sch.list_schedules(agent_id):
                if s.get("kind") == "nights":
                    continue
                st = state_of(s, now)
                if st and st["minutes_late"]:
                    who = "the doctor" if is_doctor(s) else "the desk"
                    label = f" ({s['name']})" if len(sch.list_schedules(agent_id)) > 1 else ""
                    why = f": {st['reason']}" if st["reason"] else ""
                    lines.append(f"Today {who}{label} is running {st['minutes_late']} minutes late{why}.")
            if lines:
                parts.append("(" + " ".join(lines) + " Mention it to callers asking about today; use queue_status for "
                             "'how many before me'.)")
        note = _notice_for_conversation(conversation_id)
        if note:
            parts.append(note)
        return "\n".join(parts)
    except Exception:  # noqa: BLE001 - a note never takes a turn down
        return ""


# ---------------------------------------------------------------------------------------------- "call to let them know"

_notices: Dict[str, dict] = {}     # E.164 number -> {"agent_id", "booking_id", "say", "until"}
_notice_lock = threading.Lock()


def build_notice(schedule: dict, q: dict, tok: dict, desk: str) -> Dict[str, str]:
    slot = _hhmm_dt(q["date"], tok["slot_start"])
    expected = _hhmm_dt(q["date"], tok["expected_at"])
    delay = late_line(schedule, q["minutes_late"])
    d = desk.strip() or schedule["name"]
    return {
        "en": f"Namaste, this is {d}. {delay['en']} Your {clock(slot, 'en')} visit is now around {clock(expected, 'en')}. "
              "Is that okay, or would you like another time?",
        "hi": f"नमस्ते, यह {d} से कॉल है। {delay['hi']} आपकी {clock(slot, 'hi')} की विज़िट अब लगभग {clock(expected, 'hi')} पर होगी। "
              "क्या यह ठीक है, या आप कोई और समय चाहेंगे?",
        "mr": f"नमस्कार, हा {d} कडून कॉल आहे. {delay['mr']} तुमची {clock(slot, 'mr')} ची भेट आता सुमारे {clock(expected, 'mr')} ला होईल. "
              "हे ठीक आहे का, की तुम्हाला दुसरी वेळ हवी आहे?"}


def remember_notice(number: str, agent_id: str, booking_id: str, say: Dict[str, str], now: Optional[float] = None) -> None:
    stamp = time.time() if now is None else now
    with _notice_lock:
        for k in [k for k, v in _notices.items() if v["until"] < stamp]:
            _notices.pop(k, None)
        _notices[number] = {"agent_id": agent_id, "booking_id": booking_id, "say": say, "until": stamp + NOTICE_TTL_S}


def forget_notice(number: str) -> None:
    with _notice_lock:
        _notices.pop(number, None)


def _notice_for_conversation(conversation_id: Optional[str]) -> str:
    """The notice of the call this conversation belongs to: the live phone call's other party is matched to the number the
    desk rang. "" for any other call."""
    if not conversation_id or not _notices:
        return ""
    try:
        from services import call_control  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.services import call_control  # noqa: PLC0415
    session = call_control.session_by_conversation(conversation_id)
    party = getattr(session, "party", None)
    if not party:
        return ""
    with _notice_lock:
        note = _notices.get(party)
        if not note or note["until"] < time.time():
            return ""
    say = note["say"]
    return ("(This call was placed by the desk to tell the person who answers about today's delay. As soon as they answer, "
            f"say this in their language: \"{say['en']}\" (Hindi: \"{say['hi']}\" Marathi: \"{say['mr']}\"). If they want another "
            f"time, use check_availability, then book and cancel booking {note['booking_id']} only after they say yes.)")
