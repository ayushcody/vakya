"""A profile per phone number, so the receptionist remembers a caller across calls.

`services/caller_memory.py` keeps what the caller said in ONE conversation; `services/call_records.py` keeps how a call
went. Neither knows that the person who rings today also rang last Tuesday. This keeps one small profile per (owner,
number): who they are, the language they use, when they last called and how it went, their last booking, anything the
agent noted to ask them again, and a short summary of the last call.

    display_name        the name the caller gave (the caller details form, latest wins)
    preferred_language  the language the caller asked for (the language policy's remembered choice on the conversation,
                        `conversations.reply_language`), else the language of the agent's last reply of the call
    last_call_at        UTC, ISO 8601
    last_outcome        "booked" (a booking tool succeeded), "asked" (the caller got an answer) or "unanswered" (the
                        agent said it did not know, a person was needed, or a tool failed)
    last_booking        {date, time, purpose} of the last book_appointment that succeeded and was not cancelled
    follow_ups          [{note, at}]: "ask again next time" notes the agent set with its `note_for_next_call` tool. A note is
                        offered to the agent on the next call only and is dropped when that call ends (it was asked).
    summary             one or two plain sentences composed in code at the end of the call from the caller details form and
                        the tools' results (no model call); never a number or an e-mail address
    calls               the last conversations of this caller: [{conversation_id, at, returning}] (the Calls page uses it)
    consent_note        why the profile exists and when it goes

Written when a phone call ends, and only when the caller said something (a call that ends on the greeting, or a robocall
that never speaks, leaves nothing). Read when a call starts: the agent greets a known caller by name with their last booking
(services/phone_greeting.py), the caller details form starts with their name and number (the agent does not ask again) and
the per-turn prompt carries a compact "what we know about this caller" block (`prompt_block`, backend.turn_messages).

Storage: backend/data/caller_profiles.json, mode 0600, keyed by owner and E.164 number (VAKYA_CALLER_PROFILES_FILE
overrides); hosted mode keeps them in the `caller_profiles` table (migration 021, owner-only RLS) through the Supabase
client, best effort: a failure is logged and the call goes on without a profile. Numbers are stored in full here (they are
the key); every API view shows them masked.

Settings: CALLER_PROFILES (on|off, default on), CALLER_PROFILE_RETENTION_DAYS (default 180; profiles whose last call is older are
deleted at startup), CALLER_PROFILE_END_DELAY_S (default 3: the profile is written this long after the call ends so the last
words are in the store), PHONE_RETURNING_GREETING_TIMEOUT_S (services/phone_greeting.py). The operator can delete a profile
(DELETE /api/callers/{number}); the Calls page has a "Forget this caller" button.
"""
from __future__ import annotations

import collections
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:
    from services import caller_memory, language_policy
    from services import knowledge_gaps as kg
    from services.telephony.numbers import normalise_number
except ImportError:  # pragma: no cover - imported as backend.services.caller_profiles
    from backend.services import caller_memory, language_policy
    from backend.services import knowledge_gaps as kg
    from backend.services.telephony.numbers import normalise_number

log = logging.getLogger("vakya.caller_profiles")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONSENT_NOTE = ("caller details kept to serve them on their next call; deleted after CALLER_PROFILE_RETENTION_DAYS or "
                "on request")
DEFAULT_RETENTION_DAYS = 180
MAX_CALLS = 30
MAX_FOLLOW_UPS = 3
NOTE_MAX = 160
SUMMARY_MAX = 320
OUTCOMES = ("booked", "asked", "unanswered")
BOOK, CANCEL_BOOKING = ("book_appointment", "book"), ("cancel_appointment", "cancel")   # the doctor tools, then the schedule tools

_lock = threading.RLock()


def _flag(name: str, default: str = "on") -> bool:
    return os.getenv(name, default).strip().lower() not in ("off", "0", "false", "no")


def enabled() -> bool:
    """CALLER_PROFILES (default on)."""
    return _flag("CALLER_PROFILES")


def retention_days() -> int:
    try:
        return max(1, int(float(os.getenv("CALLER_PROFILE_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS)) or DEFAULT_RETENTION_DAYS)))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def end_delay_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("CALLER_PROFILE_END_DELAY_S", "3") or 3))
    except ValueError:
        return 3.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: Any) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------------------------------- numbers

def number_of(raw: Any) -> Optional[str]:
    """A phone number as E.164 (+919876543210), or None when it is not one."""
    try:
        return normalise_number(str(raw or ""))
    except Exception:  # noqa: BLE001
        return None


def profile_id(owner: str, number: str) -> str:
    """A short stable id for the API (the full number never leaves the server): the same reference the bookings list uses
    (services/caller_ref.py), so a booking links to its caller's profile."""
    try:
        from services import caller_ref as cr  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.services import caller_ref as cr  # noqa: PLC0415
    return cr.caller_ref(owner, number) or hashlib.sha256(f"{owner}|{number}".encode("utf-8")).hexdigest()[:16]


def caller_ref(owner: str, number: str) -> str:
    """The opaque reference of a caller that screens share (the bookings list links to /desk/callers?caller=<ref>): the
    same short hash as the profile's id. It never reveals the number."""
    return profile_id(owner, number)


def mask_number(number: Any) -> str:
    digits = re.sub(r"\D", "", str(number or ""))
    return f"••••{digits[-4:]}" if len(digits) >= 5 else "••••"


def form_phone(number: str) -> str:
    """The number as the caller details form holds a phone: ten digits for an Indian number, else the digits."""
    digits = re.sub(r"\D", "", number or "")
    return digits[-10:] if number.startswith("+91") and len(digits) == 12 else digits


# ---------------------------------------------------------------------------------------------------- cleaning

def _clean(key: str, value: Any, limit: int = 80) -> Optional[str]:
    """One line, no brackets or quotes (it reaches the model's prompt), at most `limit` characters; None when empty. Same
    rules as the caller details form (`key` only names the field). Never an identity document, a card, an OTP or a password,
    and, in a reason or a note (`reason`, `other`), never a health condition (services/caller_memory.py safe_text)."""
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = unicodedata.normalize("NFC", str(value))
    text = caller_memory._BRACKETS.sub(" ", caller_memory._CONTROL.sub(" ", text))
    text = re.sub(r"\s+", " ", text).strip(" ,;:-.")
    if not text or text.lower() in ("null", "none", "n/a", "unknown", "not given", "not provided", "-"):
        return None
    if caller_memory.safe_text(text, health=key in ("reason", "other")) is None:
        return None
    return text[:limit].strip() or None


def _clean_booking(raw: Any) -> Optional[Dict[str, str]]:
    if not isinstance(raw, dict):
        return None
    out: Dict[str, str] = {}
    d = _clean("date", raw.get("date"))
    if d and caller_memory._valid_iso_date(d):
        out["date"] = d
    t = _clean("time", raw.get("time"))
    if t and caller_memory._valid_hhmm(t):
        out["time"] = t if len(t) == 5 else "0" + t
    p = _clean("reason", raw.get("purpose"), 100)
    if p:
        out["purpose"] = p
    return out if ("date" in out or "time" in out) else None


BOOKING_STATUSES = ("booked", "rescheduled", "cancelled")
BOOKINGS_MAX = 8


def _clean_bookings(raw: Any) -> List[Dict[str, Any]]:
    """[{booking_id, date, time, status booked|rescheduled|cancelled, previous?: {date, time}, changed_at?}]: the caller's
    bookings as the live booking store last showed them (`sync_bookings`)."""
    out: List[Dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        b = _clean_booking(item)
        if not b:
            continue
        row: Dict[str, Any] = {k: v for k, v in b.items() if k in ("date", "time")}
        bid = _clean("other", item.get("booking_id"), 16)
        if bid:
            row["booking_id"] = bid
        row["status"] = item.get("status") if item.get("status") in BOOKING_STATUSES else "booked"
        prev = _clean_booking(item.get("previous"))
        if prev:
            row["previous"] = {k: v for k, v in prev.items() if k in ("date", "time")}
        if item.get("changed_at") and _parse_iso(item.get("changed_at")):
            row["changed_at"] = str(item["changed_at"])
        out.append(row)
    return out[-BOOKINGS_MAX:]


def _clean_follow_ups(raw: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for item in raw if isinstance(raw, list) else []:
        note = _clean("other", item.get("note") if isinstance(item, dict) else item, NOTE_MAX)
        if note and note.lower() not in {o["note"].lower() for o in out}:
            at = item.get("at") if isinstance(item, dict) and isinstance(item.get("at"), str) else _now_iso()
            out.append({"note": note, "at": at})
    return out[:MAX_FOLLOW_UPS]


PEOPLE_MAX = 8
FACTS_MAX = caller_memory.MAX_FACTS
SOURCES = ("phone", "web call", "owner")
_RELATIONS = set(caller_memory._RELATION_WORDS.values()) | {"self", "other"}


def _clean_facts(raw: Any) -> Dict[str, str]:
    """{key: value} of the trade details kept for the caller (a party size, a preferred stylist): keys are plain words, values
    one clean line with no health condition, no identity number and no long digit run."""
    out: Dict[str, str] = {}
    for k, v in (raw.items() if isinstance(raw, dict) else []):
        key = str(k)
        if not caller_memory._FACT_KEY.fullmatch(key):
            continue
        val = _clean("other", v, 100)
        if val and not re.search(r"\d{9,}", re.sub(r"[ -]", "", val)):
            out[key] = val
    return dict(list(out.items())[-FACTS_MAX:])


def _clean_people(raw: Any) -> List[Dict[str, Any]]:
    """[{name, relation?, age_band?, facts?, last_booking?, last_booked_at?}]: the people booked for on this number (at most
    PEOPLE_MAX). `relation` is to the caller, stored in English (mother, father, son, daughter, wife, husband, self, other, ...).
    An older profile has only name, relation, age_band and last_booked_at: it reads the same way and is written in this shape
    the next time it is saved."""
    out: List[Dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = _clean("name", item.get("name"), 60)
        if not name or any(c.isdigit() for c in name) or name.lower() in {o["name"].lower() for o in out}:
            continue
        person: Dict[str, Any] = {"name": name}
        rel = str(item.get("relation") or "").strip().lower()
        if rel in _RELATIONS:
            person["relation"] = rel
        band = str(item.get("age_band") or "").strip().lower()
        if band in caller_memory.AGE_BANDS:
            person["age_band"] = band
        booked = item.get("last_booked_at")
        if isinstance(booked, str) and _parse_iso(booked):
            person["last_booked_at"] = booked
        facts = _clean_facts(item.get("facts"))
        if facts:
            person["facts"] = facts
        last = _clean_booking(item.get("last_booking"))
        if last:
            person["last_booking"] = last
        out.append(person)
    return out[-PEOPLE_MAX:]


def clean_profile(raw: Dict[str, Any]) -> Dict[str, Any]:
    """A stored profile with every value made safe again (a hand-edited file or an old version must not reach the prompt)."""
    people = _clean_people(raw.get("people"))
    last_person = _clean("name", raw.get("last_person"), 60)
    owner_, number_ = str(raw.get("owner") or ""), str(raw.get("number") or "")
    out: Dict[str, Any] = {
        "id": profile_id(owner_, number_) if owner_ and number_ else str(raw.get("id") or ""), "owner": owner_, "number": number_,
        "display_name": _clean("name", raw.get("display_name")),
        "preferred_language": (_clean("other", raw.get("preferred_language"), 16) or "").lower() or None,
        "last_call_at": raw.get("last_call_at") if _parse_iso(raw.get("last_call_at")) else None,
        "last_outcome": raw.get("last_outcome") if raw.get("last_outcome") in OUTCOMES else None,
        "last_booking": _clean_booking(raw.get("last_booking")),
        "bookings": _clean_bookings(raw.get("bookings")),
        "follow_ups": _clean_follow_ups(raw.get("follow_ups")),
        "summary": _clean("other", raw.get("summary"), SUMMARY_MAX),
        "facts": _clean_facts(raw.get("facts")),
        "people": people,
        "last_person": last_person if last_person and any(p["name"].lower() == last_person.lower() for p in people) else None,
        "source": raw.get("source") if raw.get("source") in SOURCES else "phone",
        "notice_given": bool(raw.get("notice_given")),
        "call_count": int(raw.get("call_count") or 0) if str(raw.get("call_count") or "0").isdigit() else 0,
        "calls": [{"conversation_id": str(c.get("conversation_id")), "at": c.get("at"), "returning": bool(c.get("returning"))}
                  for c in (raw.get("calls") or []) if isinstance(c, dict) and c.get("conversation_id")][-MAX_CALLS:],
        "created_at": raw.get("created_at") or _now_iso(), "updated_at": raw.get("updated_at") or _now_iso(),
        "consent_note": CONSENT_NOTE,
    }
    return out


# ---------------------------------------------------------------------------------------------------- stores

def file_path() -> str:
    return os.getenv("VAKYA_CALLER_PROFILES_FILE", "").strip() or os.path.join(BASE_DIR, "data", "caller_profiles.json")


def _key(owner: str, number: str) -> str:
    return f"{owner}|{number}"


class LocalFileStore:
    """All profiles in one JSON file (mode 0600, atomic writes), keyed by owner and number."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or file_path()

    def _read(self) -> Dict[str, dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: Dict[str, dict]) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            if os.name != "nt":
                os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, owner: str, number: str) -> Optional[dict]:
        with _lock:
            return self._read().get(_key(owner, number))

    def put(self, profile: dict) -> None:
        with _lock:
            data = self._read()
            data[_key(profile["owner"], profile["number"])] = profile
            self._write(data)

    def list(self, owner: str) -> List[dict]:
        with _lock:
            return [p for p in self._read().values() if isinstance(p, dict) and p.get("owner") == owner]

    def delete(self, owner: str, number: str) -> bool:
        with _lock:
            data = self._read()
            if data.pop(_key(owner, number), None) is None:
                return False
            self._write(data)
            return True

    def prune(self, cutoff_iso: str) -> int:
        """Delete every profile whose last call (else its last update) is before `cutoff_iso`. Returns how many."""
        cutoff = _parse_iso(cutoff_iso)
        with _lock:
            data = self._read()
            old = [k for k, p in data.items()
                   if cutoff is not None and (_parse_iso((p or {}).get("last_call_at") or (p or {}).get("updated_at")) or cutoff) < cutoff]
            for k in old:
                data.pop(k, None)
            if old:
                self._write(data)
            return len(old)


class SupabaseStore:
    """The hosted twin: table `caller_profiles` (migration 021): user_id, number, last_call_at and the whole profile as
    json. Every query filters by user_id. Best effort: callers catch what it raises."""

    TABLE = "caller_profiles"

    def __init__(self, client: Any):
        self.client = client

    def _t(self) -> Any:
        return self.client.table(self.TABLE)

    def get(self, owner: str, number: str) -> Optional[dict]:
        rows = self._t().select("data").eq("user_id", owner).eq("number", number).limit(1).execute().data or []
        return rows[0].get("data") if rows else None

    def put(self, profile: dict) -> None:
        self._t().upsert({"user_id": profile["owner"], "number": profile["number"],
                          "last_call_at": profile.get("last_call_at"), "data": profile},
                         on_conflict="user_id,number").execute()

    def list(self, owner: str) -> List[dict]:
        rows = (self._t().select("data").eq("user_id", owner).order("last_call_at", desc=True).limit(2000)
                .execute().data or [])
        return [r["data"] for r in rows if isinstance(r.get("data"), dict)]

    def delete(self, owner: str, number: str) -> bool:
        rows = self._t().delete().eq("user_id", owner).eq("number", number).execute().data or []
        return bool(rows)

    def prune(self, cutoff_iso: str) -> int:
        rows = self._t().delete().lt("last_call_at", cutoff_iso).execute().data or []
        return len(rows)


_supabase_getter: Optional[Callable[[], Any]] = None
_local_store: Optional[LocalFileStore] = None


def configure(*, supabase: Optional[Callable[[], Any]] = None) -> None:
    """Where the hosted client comes from (routers/callers.py sets it from the backend module); tests set a fake."""
    global _supabase_getter
    _supabase_getter = supabase


def get_store(mode: str = "local") -> Any:
    """The store of a storage mode. Hosted mode without a Supabase client uses the local file."""
    global _local_store
    if mode != "local" and _supabase_getter is not None:
        try:
            client = _supabase_getter()
        except Exception:  # noqa: BLE001
            client = None
        if client is not None:
            return SupabaseStore(client)
    with _lock:
        path = file_path()
        if _local_store is None or _local_store.path != path:
            _local_store = LocalFileStore(path)
        return _local_store


# ---------------------------------------------------------------------------------------------------- reading

def lookup(owner: str, number: Optional[str], mode: str = "local") -> Optional[Dict[str, Any]]:
    """The caller's profile, or None (also when CALLER_PROFILES is off, the number is not valid, the profile is older than
    the retention or the store fails). Blocking: run it with asyncio.to_thread."""
    if not enabled() or not owner:
        return None
    n = number_of(number)
    if not n:
        return None
    try:
        raw = get_store(mode).get(owner, n)
    except Exception as e:  # noqa: BLE001 - a missing profile is never worth failing a call
        log.warning("could not read a caller profile: %s", e.__class__.__name__)
        return None
    if not isinstance(raw, dict):
        return None
    profile = clean_profile({**raw, "owner": owner, "number": n})
    profile["id"] = profile["id"] or profile_id(owner, n)
    last = _parse_iso(profile.get("last_call_at") or profile.get("updated_at"))
    if last is not None and datetime.now(timezone.utc) - last > timedelta(days=retention_days()):
        return None
    return profile


def find(owner: str, key: str, mode: str = "local") -> Optional[Dict[str, Any]]:
    """A profile by the number (any common spelling) or by its API id. Blocking."""
    n = number_of(key)
    if n:
        return lookup_any_age(owner, n, mode)
    for p in list_profiles(owner, mode):
        if p.get("id") == key:
            return p
    return None


def lookup_any_age(owner: str, number: str, mode: str = "local") -> Optional[Dict[str, Any]]:
    raw = get_store(mode).get(owner, number)
    if not isinstance(raw, dict):
        return None
    p = clean_profile({**raw, "owner": owner, "number": number})
    p["id"] = p["id"] or profile_id(owner, number)
    return p


def list_profiles(owner: str, mode: str = "local") -> List[Dict[str, Any]]:
    """The owner's profiles, newest call first. Blocking."""
    out = []
    for raw in get_store(mode).list(owner):
        if not isinstance(raw, dict) or not raw.get("number"):
            continue
        p = clean_profile({**raw, "owner": owner})
        p["id"] = p["id"] or profile_id(owner, p["number"])
        out.append(p)
    out.sort(key=lambda p: p.get("last_call_at") or p.get("updated_at") or "", reverse=True)
    return out


def delete(owner: str, key: str, mode: str = "local") -> bool:
    """Forget a caller (the number in any spelling, or the API id). Also drops what a live call knows about them."""
    p = find(owner, key, mode)
    if p is None:
        return False
    ok = get_store(mode).delete(owner, p["number"])
    with _lock:
        for cid in [c for c, v in _CONTEXT.items() if v.get("owner") == owner and v.get("number") == p["number"]]:
            _CONTEXT.pop(cid, None)
    return ok


def prune_expired(mode: str = "local") -> int:
    """Delete the profiles whose last call is older than CALLER_PROFILE_RETENTION_DAYS (called once at startup). Never raises."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days())).strftime("%Y-%m-%dT%H:%M:%SZ")
        n = get_store(mode).prune(cutoff)
        if n:
            log.info("caller profiles: deleted %d older than %d days", n, retention_days())
        return n
    except Exception as e:  # noqa: BLE001
        log.warning("could not prune the caller profiles: %s", e.__class__.__name__)
        return 0


def fact_label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def public_person(person: Dict[str, Any]) -> Dict[str, Any]:
    """One person of the family for the owner's screens: name, relation to the caller, age band, the usual details as labelled
    chips and the last booking, each only when there is something to show."""
    out = {k: person[k] for k in ("name", "relation", "age_band", "last_booked_at", "last_booking") if person.get(k)}
    if person.get("facts"):
        out["facts"] = [{"key": k, "label": fact_label(k), "value": v} for k, v in person["facts"].items()]
    return out


def public_view(profile: Dict[str, Any]) -> Dict[str, Any]:
    """A profile for the owner's screens: the number masked, the id (= `caller_ref`) for later calls."""
    days = retention_days()
    last = _parse_iso(profile.get("last_call_at") or profile.get("updated_at"))
    pid = profile.get("id") or profile_id(profile.get("owner", ""), profile.get("number", ""))
    return {
        "id": pid, "caller_ref": pid,
        "number_masked": mask_number(profile.get("number")),
        "display_name": profile.get("display_name"), "preferred_language": profile.get("preferred_language"),
        "last_call_at": profile.get("last_call_at"), "last_outcome": profile.get("last_outcome"),
        "last_booking": profile.get("last_booking"), "follow_ups": profile.get("follow_ups") or [],
        "summary": profile.get("summary"), "call_count": profile.get("call_count") or 0,
        "facts": [{"key": k, "label": fact_label(k), "value": v} for k, v in (profile.get("facts") or {}).items()],
        "people": [public_person(x) for x in profile.get("people") or []], "last_person": profile.get("last_person"),
        "source": profile.get("source") or "phone",
        "calls": profile.get("calls") or [], "consent_note": CONSENT_NOTE, "retention_days": days,
        "expires_at": (last + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ") if last else None,
    }


# ---------------------------------------------------------------------------------------------------- the call

_WEEKDAYS = {
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "hi": ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
    "mr": ["सोमवारी", "मंगळवारी", "बुधवारी", "गुरुवारी", "शुक्रवारी", "शनिवारी", "रविवारी"],
    "hinglish": ["Somvaar", "Mangalvaar", "Budhvaar", "Guruvaar", "Shukravaar", "Shanivaar", "Ravivaar"],
}
_MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"],
    "hi": ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
    "mr": ["जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून", "जुलै", "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर", "नोव्हेंबर",
           "डिसेंबर"],
    "hinglish": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                 "November", "December"],
}
GREETING_LANGUAGES = ("hi", "mr", "en", "hinglish")


def _day_phrase(d: date, lang: str, today: date) -> str:
    """The weekday for a day within a week of today, else "12 October" (the weekday alone would name the wrong day)."""
    if abs((d - today).days) <= 6:
        return _WEEKDAYS[lang][d.weekday()]
    month = _MONTHS[lang][d.month - 1]
    if lang == "mr":
        return f"{d.day} {month}ला"
    return f"{d.day} {month}"


def _time_phrase(hhmm: str, lang: str) -> str:
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    h12 = h % 12 or 12
    clock = f"{h12}" if m == 0 else f"{h12}:{m:02d}"
    if lang == "en":
        return f"{clock} {'AM' if h < 12 else 'PM'}"
    return clock


_NEAR_DAYS = {   # an upcoming booking today or tomorrow is spoken as such, not as a weekday
    "en": ("today", "tomorrow"), "hi": ("आज", "कल"), "mr": ("आज", "उद्या"), "hinglish": ("aaj", "kal"),
}


def _is_upcoming(booking: Optional[Dict[str, str]], now: datetime) -> bool:
    """True when the booking's date is after today, or is today at or after the current time (no time: the whole day)."""
    if not booking or not booking.get("date") or not caller_memory._valid_iso_date(booking["date"]):
        return False
    d = datetime.strptime(booking["date"], "%Y-%m-%d").date()
    if d != now.date():
        return d > now.date()
    hhmm = booking.get("time") or ""
    return not hhmm or hhmm[:5] >= now.strftime("%H:%M")


def _booking_words(booking: Optional[Dict[str, str]], lang: str, today: date,
                   upcoming: bool = False) -> Optional[Tuple[str, str]]:
    """(day phrase, time phrase) of a booking in `lang`; either may be empty; None when there is nothing to say. An upcoming
    booking today or tomorrow says so ("today", "आज", "उद्या")."""
    if not booking:
        return None
    day = ""
    if booking.get("date") and caller_memory._valid_iso_date(booking["date"]):
        d = datetime.strptime(booking["date"], "%Y-%m-%d").date()
        ahead = (d - today).days
        day = _NEAR_DAYS[lang][ahead] if upcoming and ahead in (0, 1) else _day_phrase(d, lang, today)
    at = _time_phrase(booking["time"], lang) if booking.get("time") else ""
    return (day, at) if (day or at) else None


def greeting_language(profile: Dict[str, Any], supported: Optional[Sequence[str]]) -> str:
    """The language of a returning caller's greeting: their own when the agent supports it and the greeting has a form for
    it (Marathi falls back to Hindi), else the agent's first language that has one, else English."""
    codes = [str(c).split("-")[0].lower() for c in (supported or ["en"])] or ["en"]
    pref = str(profile.get("preferred_language") or "").split("-")[0].lower()

    def ok(lang: str) -> bool:
        return lang in GREETING_LANGUAGES and (lang in codes or (lang == "hinglish" and "hi" in codes))
    if ok(pref):
        return pref
    if pref == "mr" and ok("hi"):      # the nearest language the agent speaks (the same script)
        return "hi"
    return next((c for c in codes if ok(c)), "en")


REMEMBER_NOTICE = {
    "hi": "आपकी जानकारी मेरे पास सुरक्षित रहती है ताकि अगली बार आसानी हो; हटवाना हो तो बस कह दीजिए।",
    "mr": "तुमची माहिती पुढच्या वेळी सोपे व्हावे म्हणून लक्षात ठेवली जाते; काढायची असल्यास फक्त सांगा.",
    "hinglish": "Aapki details yaad rakhi jaati hain taaki agli baar aasaani ho; hatwani ho to bas keh dijiye.",
    "en": "I keep your details to help next time; just say so if you would like them deleted.",
}


def family_question(profile: Dict[str, Any], lang: str) -> Optional[str]:
    """The question a returning caller who books for somebody else is asked in place of "how can I help?" (first name only, with
    "ji" in Hindi and Marathi): None when the last booking was for the caller themselves or nobody is known."""
    person = asked_person(profile)
    if not person:
        return None
    first = person["name"].split()[0]
    return {
        "hi": f"क्या आज भी {first} जी के लिए बुक करना है, या किसी और के लिए?",
        "mr": f"आज {first} जी यांच्यासाठीच बुक करायचे आहे का, की दुसऱ्या कोणासाठी?",
        "hinglish": f"Kya aaj bhi {first} ji ke liye book karna hai, ya kisi aur ke liye?",
        "en": f"Is this for {first} again, or for someone else?",
    }.get(lang)


def live_bookings(number: Any, agent_id: Optional[str] = None, now: Optional[datetime] = None,
                  rows: Optional[Sequence[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The caller's bookings that are still ahead, from the live booking store (not the profile, which is only written when
    a call ends and so is stale after a reschedule or a cancel), soonest first. Cancelled ones are left out. Best effort: []
    when the store cannot be read."""
    try:
        digits = re.sub(r"\D", "", str(number or ""))[-10:]
        if len(digits) != 10:
            return []
        if rows is None:
            try:
                from mcp_servers import appointments  # noqa: PLC0415
            except ImportError:  # pragma: no cover
                from backend.mcp_servers import appointments  # noqa: PLC0415
            rows = appointments.all_bookings()
        now = now or datetime.now()
        out = []
        for b in rows:
            if b.get("status") == "cancelled" or re.sub(r"\D", "", str(b.get("phone") or ""))[-10:] != digits:
                continue
            if agent_id and b.get("agent_id") and b.get("agent_id") != agent_id:
                continue
            try:
                start = datetime.fromisoformat(str(b.get("start") or f"{b['date']}T{b.get('time') or '00:00'}"))
            except (KeyError, ValueError):
                continue
            end = None
            try:
                end = datetime.fromisoformat(str(b["end"])) if b.get("end") else None
            except ValueError:
                pass
            if (end or start) > now:
                out.append((start, b))
        out.sort(key=lambda x: x[0])
        return [b for _, b in out]
    except Exception:  # noqa: BLE001
        log.exception("live bookings unavailable")
        return []


def with_live_booking(profile: Dict[str, Any], agent_id: Optional[str] = None, now: Optional[datetime] = None,
                      rows: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """A copy of the profile whose `last_booking` is the nearest upcoming non-cancelled booking in the live store (None when
    the caller has none ahead: a cancelled booking is never greeted; a booking that has passed stays as "last time"). The profile
    on disk is not changed."""
    live = live_bookings(profile.get("number"), agent_id, now, rows)
    out = dict(profile)
    if live:
        b = live[0]
        out["last_booking"] = {k: v for k, v in (("date", b.get("date")), ("time", b.get("time")),
                                                  ("purpose", b.get("reason") or None)) if v} or None
    elif _is_upcoming(profile.get("last_booking"), now or datetime.now()):
        out["last_booking"] = None      # the profile says it is still ahead but the store has no live booking: moved or cancelled
    return out


def _row_start(b: Dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(b.get("start") or f"{b['date']}T{b.get('time') or '00:00'}"))
    except (KeyError, ValueError):
        return None


def _store_rows(number: Any, agent_id: Optional[str], rows: Optional[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    digits = re.sub(r"\D", "", str(number or ""))[-10:]
    if len(digits) != 10:
        return []
    if rows is None:
        try:
            from mcp_servers import appointments  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            from backend.mcp_servers import appointments  # noqa: PLC0415
        rows = appointments.all_bookings()
    return [b for b in rows if re.sub(r"\D", "", str(b.get("phone") or ""))[-10:] == digits
            and not (agent_id and b.get("agent_id") and b.get("agent_id") != agent_id)]


def sync_bookings(profile: Dict[str, Any], agent_id: Optional[str] = None, now: Optional[datetime] = None,
                  rows: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The profile brought in line with the live booking store: `bookings` lists the caller's bookings that are still ahead
    (and ones cancelled or moved that were, so the agent knows: "A28 ... rescheduled from 5 PM", "A27 ... cancelled") with an
    explicit status, `last_booking` is the nearest upcoming non-cancelled booking, and a person's `last_booking` that was
    moved or cancelled follows it. Returns a new dict; the store is not written (`sync_profile` does that). A number with no
    booking in the store leaves the profile as it is."""
    now = now or datetime.now()
    try:
        mine = _store_rows(profile.get("number"), agent_id, rows)
    except Exception:  # noqa: BLE001
        log.exception("booking store unavailable")
        return profile
    if not mine:
        return profile
    items: List[Tuple[datetime, Dict[str, Any]]] = []
    for b in mine:
        start = _row_start(b)
        if start is None or (start + timedelta(days=1)) <= now and not b.get("moved_from") and b.get("status") != "cancelled":
            continue
        if b.get("status") == "cancelled":
            status = "cancelled"
        elif b.get("moved_from"):
            status = "rescheduled"
        else:
            status = "booked"
        if status != "booked" and start.date() < now.date():
            continue                                    # a past one that was moved or cancelled is not worth saying
        item: Dict[str, Any] = {"booking_id": b.get("booking_id"), "date": b.get("date"), "time": b.get("time"), "status": status}
        if status == "rescheduled":
            item["previous"] = {"date": b["moved_from"].get("date"), "time": b["moved_from"].get("time")}
            item["changed_at"] = b.get("moved_at")
        elif status == "cancelled":
            item["changed_at"] = b.get("cancelled_at")
        items.append((start, item))
    items.sort(key=lambda x: x[0])
    out = dict(profile)
    out["bookings"] = _clean_bookings([i for _, i in items][-BOOKINGS_MAX:])
    ahead = live_bookings(profile.get("number"), agent_id, now, mine)
    if ahead:
        b = ahead[0]
        out["last_booking"] = _clean_booking({"date": b.get("date"), "time": b.get("time"), "purpose": b.get("reason") or None})
    elif _is_upcoming(profile.get("last_booking"), now):
        out["last_booking"] = None
    people = []
    for person in profile.get("people") or []:
        person = dict(person)
        last = person.get("last_booking") or {}
        for it in out["bookings"]:
            if it["status"] == "booked":
                continue
            old = it.get("previous") if it["status"] == "rescheduled" else {"date": it.get("date"), "time": it.get("time")}
            if old and last.get("date") == old.get("date") and last.get("time") == old.get("time"):
                if it["status"] == "rescheduled":
                    person["last_booking"] = {**last, "date": it["date"], "time": it["time"]}
                else:
                    person.pop("last_booking", None)
                break
        people.append(person)
    out["people"] = people
    return out


def _atomic(fn: Callable) -> Callable:
    """Run a read-modify-write of one profile (find, change, put) under the module lock, so two calls of the same number that
    end together, or an owner's edit that lands during a call's write, never overwrite each other's change (the store's own
    lock covers only each single read or write)."""
    import functools  # noqa: PLC0415

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with _lock:
            return fn(*args, **kwargs)
    return wrapper


@_atomic
def sync_profile(owner: str, number: Any, agent_id: Optional[str] = None, mode: str = "local",
                 rows: Optional[Sequence[Dict[str, Any]]] = None, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Repair the stored profile of a number from the live booking store and write it back when it changed (the one-off sync:
    called at the start of a phone call and whenever a booking is moved or cancelled). Blocking; never raises. Returns the
    profile (None when there is none)."""
    n = number_of(number)
    if not enabled() or not n or not owner:
        return None
    try:
        existing = lookup_any_age(owner, n, mode)
        if existing is None:
            return None
        fixed = sync_bookings(existing, agent_id, now, rows)
        if fixed.get("bookings") != existing.get("bookings") or fixed.get("last_booking") != existing.get("last_booking") \
                or fixed.get("people") != existing.get("people"):
            fixed = clean_profile({**fixed, "updated_at": _now_iso()})
            fixed["id"] = fixed["id"] or profile_id(owner, n)
            get_store(mode).put(fixed)
            log.info("caller profile bookings synced (%s)", mask_number(n))
        return fixed
    except Exception as e:  # noqa: BLE001
        log.warning("could not sync a caller profile: %s", e.__class__.__name__)
        return None


def sync_number(number: Any, agent_id: Optional[str] = None) -> None:
    """Sync every local profile of a number (a booking was moved or cancelled by anyone: the phone agent, staff or the desk
    UI). Best effort, local file store; hosted profiles are synced at the start of the caller's next call."""
    n = number_of(number)
    if not enabled() or not n:
        return
    try:
        for raw in list(get_store("local")._read().values()):   # noqa: SLF001
            if isinstance(raw, dict) and raw.get("number") == n and raw.get("owner"):
                sync_profile(raw["owner"], n, agent_id)
    except Exception as e:  # noqa: BLE001
        log.warning("could not sync caller profiles: %s", e.__class__.__name__)


def returning_greeting(profile: Optional[Dict[str, Any]], supported: Optional[Sequence[str]] = None,
                       today: Optional[date] = None, notice: bool = False,
                       now: Optional[datetime] = None) -> Optional[Tuple[str, str]]:
    """(language, words) the agent says to a known caller instead of its usual greeting, or None when the profile has
    neither a name nor a last booking to speak of (then the agent's own greeting plays). `notice` adds, once, that the agent
    keeps the caller's details to help next time and how to have them deleted (REMEMBER_NOTICE).

    A booking that is still ahead is named as the caller's appointment; one that has passed as the last one:

        en  Hello Priya, welcome back. Your appointment is on Friday at 3 PM. How can I help today?
        hi  नमस्ते प्रिया जी, फिर से आपका स्वागत है। आपकी अपॉइंटमेंट कल 3 बजे है। आज कैसे मदद करूँ?
        en  Hello Priya, last time you booked an appointment for Tuesday at 4 PM. How can I help today?
        mr  नमस्कार प्रिया जी, मागच्या वेळी तुम्ही मंगळवारी 4 वाजताची अपॉइंटमेंट घेतली होती. आज कशी मदत करू?

    `now` (default: the clock) decides "still ahead"; `today` alone means the start of that day.
    """
    if not profile:
        return None
    name = profile.get("display_name")
    if now is None:
        now = datetime.combine(today, datetime.min.time()) if today else datetime.now()
    today = today or now.date()
    lang = greeting_language(profile, supported)
    booking = profile.get("last_booking")
    upcoming = _is_upcoming(booking, now)
    words = _booking_words(booking, lang, today, upcoming)
    if not name and not words:
        return None
    if lang == "hi":
        who = f"नमस्ते {name} जी" if name else "नमस्ते"
        ask = "आज कैसे मदद करूँ?"
        slot = " ".join(w for w in (words[0], f"{words[1]} बजे" if words[1] else "") if w) if words else ""
        if words and upcoming:
            lead = f"{who}, फिर से आपका स्वागत है। आपकी अपॉइंटमेंट {slot} है।"
        elif words:
            lead = f"{who}, पिछली बार आपने {slot} की अपॉइंटमेंट ली थी।"
        else:
            lead = f"{who}, फिर से आपका स्वागत है।"
    elif lang == "mr":
        who = f"नमस्कार {name} जी" if name else "नमस्कार"
        ask = "आज कशी मदत करू?"
        if words and upcoming:
            slot = " ".join(w for w in (words[0], f"{words[1]} वाजता" if words[1] else "") if w)
            lead = f"{who}, पुन्हा स्वागत आहे. तुमची अपॉइंटमेंट {slot} आहे."
        elif words:
            slot = " ".join(w for w in (words[0], f"{words[1]} वाजताची" if words[1] else "") if w)
            lead = f"{who}, मागच्या वेळी तुम्ही {slot} अपॉइंटमेंट घेतली होती."
        else:
            lead = f"{who}, पुन्हा स्वागत आहे."
    elif lang == "hinglish":
        who = f"Namaste {name} ji" if name else "Namaste"
        ask = "Aaj kaise madad karoon?"
        slot = " ".join(w for w in (words[0], f"{words[1]} baje" if words[1] else "") if w) if words else ""
        if words and upcoming:
            lead = f"{who}, phir se swagat hai. Aapki appointment {slot} hai."
        elif words:
            lead = f"{who}, pichli baar aapne {slot} ki appointment li thi."
        else:
            lead = f"{who}, phir se swagat hai."
    else:
        lang = "en"
        who = f"Hello {name}" if name else "Hello"
        ask = "How can I help today?"
        if words:
            slot = f"{words[0]} at {words[1]}" if words[0] and words[1] else (words[0] or f"{words[1]}")
        if words and upcoming:
            near = words[0] in _NEAR_DAYS["en"] or not words[0]
            lead = f"{who}, welcome back. Your appointment is {'' if near else 'on '}{slot}."
        elif words:
            lead = f"{who}, last time you booked an appointment for {slot}."
        else:
            lead = f"{who}, welcome back."
    ask = family_question(profile, lang) or ask
    return lang, " ".join(x for x in (lead, REMEMBER_NOTICE[lang] if notice else "", ask) if x)


def callback_greeting_language(profile: Optional[Dict[str, Any]], supported: Optional[Sequence[str]]) -> Optional[str]:
    return greeting_language(profile, supported) if profile and profile.get("display_name") else None


def _when_for_prompt(booking: Dict[str, str]) -> str:
    bits = []
    if booking.get("date"):
        try:
            d = datetime.strptime(booking["date"], "%Y-%m-%d")
            bits.append(f"{d.strftime('%A')} {booking['date']}")
        except ValueError:
            pass
    if booking.get("time"):
        bits.append(f"at {booking['time']}")
    return " ".join(bits)


def number_hint(number: Any) -> Optional[str]:
    """"98xxxx3210" for an Indian mobile number (the form the agent confirms a number in), else the last four digits."""
    digits = form_phone(str(number or ""))
    if len(digits) == 10:
        return f"{digits[:2]}xxxx{digits[-4:]}"
    return f"ending {digits[-4:]}" if len(digits) >= 4 else None


AGE_WORDS = {"senior": "60+", "adult": "adult", "teen": "teen", "child": "child"}
DOCTOR_KEYS = ("preferred_doctor", "usual_doctor", "doctor")


def people_text(people: Sequence[Dict[str, Any]]) -> str:
    bits = []
    for p in people:
        extra = ", ".join(x for x in (p.get("relation"), p.get("age_band")) if x)
        bits.append(p["name"] + (f" ({extra})" if extra else ""))
    return ", ".join(bits)


def _short_date(iso: Optional[str]) -> str:
    try:
        d = datetime.strptime(str(iso), "%Y-%m-%d")
    except ValueError:
        return ""
    return f"{d.day} {d.strftime('%b')}"


def people_by_recency(people: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The people of a number, the one booked for last first. Stored oldest first; a person with no booking time keeps their
    place by being treated as older than anybody with one."""
    indexed = list(enumerate(people))
    indexed.sort(key=lambda ip: (ip[1].get("last_booked_at") or "", ip[0]), reverse=True)
    return [p for _i, p in indexed]


def person_line(p: Dict[str, Any]) -> str:
    """One person of the family for the prompt: "Sunita Kulkarni (mother, 60+, usually Dr. Joshi, last visit 12 Oct, follow-up)"."""
    bits: List[str] = []
    if p.get("relation"):
        bits.append(p["relation"])
    if p.get("age_band"):
        bits.append(AGE_WORDS.get(p["age_band"], p["age_band"]))
    facts = p.get("facts") or {}
    doctor = next((facts[k] for k in DOCTOR_KEYS if facts.get(k)), None)
    if doctor:
        bits.append(f"usually {doctor}")
    for k, v in facts.items():
        if k not in DOCTOR_KEYS and len(bits) < 6:
            bits.append(f"{k.replace('_', ' ')} {v}")
    last = p.get("last_booking") or {}
    when = _short_date(last.get("date"))
    if when:
        bits.append(f"last visit {when}" + (f", {last['purpose']}" if last.get("purpose") else ""))
    return p["name"] + (f" ({', '.join(bits)})" if bits else "")


def family_text(profile: Dict[str, Any], limit: int = 4) -> str:
    """"This number usually books for: ..." (the family, newest first), or "" when nobody is booked for yet."""
    people = people_by_recency(profile.get("people") or [])[:limit]
    if not people:
        return ""
    return "this number usually books for: " + "; ".join(person_line(p) for p in people)


def asked_person(profile: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The person to offer first on a call: the last one booked for who is not the caller, else None."""
    for p in people_by_recency(profile.get("people") or []):
        if p.get("relation") != "self" and p.get("last_booked_at"):
            return p
    return None


def _clock12(t: Optional[str]) -> str:
    try:
        h, m = int(t[:2]), int(t[3:5])
    except (TypeError, ValueError):
        return ""
    return f"{h % 12 or 12}{f':{m:02d}' if m else ''} {'AM' if h < 12 else 'PM'}"


def _day_short(d: Optional[str]) -> str:
    try:
        x = datetime.strptime(str(d), "%Y-%m-%d")
    except ValueError:
        return ""
    return f"{x.strftime('%a')} {x.day} {x.strftime('%b')}"


def _bookings_line(bookings: Sequence[Dict[str, Any]]) -> str:
    """"Bookings: A28 Tue 10 Nov 6 PM (rescheduled from 5 PM); A27 Mon 9 Nov 5 PM (cancelled)": live ones first."""
    if not bookings:
        return ""
    order = {"booked": 0, "rescheduled": 0, "cancelled": 1}
    bits = []
    for b in sorted(bookings, key=lambda x: (order.get(x.get("status"), 0), x.get("date") or "", x.get("time") or "")):
        what = " ".join(x for x in (_day_short(b.get("date")), _clock12(b.get("time"))) if x)
        note = ""
        if b.get("status") == "rescheduled":
            prev = b.get("previous") or {}
            was = _clock12(prev.get("time")) if prev.get("date") == b.get("date") else \
                " ".join(x for x in (_day_short(prev.get("date")), _clock12(prev.get("time"))) if x)
            note = f" (rescheduled from {was})" if was else " (rescheduled)"
        elif b.get("status") == "cancelled":
            note = " (cancelled)"
        bits.append(f"{b['booking_id'] + ' ' if b.get('booking_id') else ''}{what}{note}")
    return "Bookings: " + "; ".join(bits)


def _upcoming_line(upcoming: Sequence[Dict[str, Any]]) -> str:
    bits = []
    for b in list(upcoming)[:4]:
        what = _when_for_prompt({"date": b.get("date"), "time": b.get("time")})
        if what.strip():
            bits.append(f"{b.get('booking_id') + ' ' if b.get('booking_id') else ''}{what.strip()}")
    return "upcoming bookings of this caller (live): " + "; ".join(bits) if bits else ""


def block_text(profile: Dict[str, Any], upcoming: Optional[Sequence[Dict[str, Any]]] = None) -> str:
    """The compact "what we know about this caller" block for the caller's message of every turn of the call. It leads with the
    family (the people this number books for), then the caller's own details. Values are caller data (cleaned, one line);
    the model is told to confirm what it knows in one short question rather than ask from scratch, to read a booking back
    before it makes it, and never to read the block out."""
    parts: List[str] = []
    family = family_text(profile)
    if family:
        parts.append(family + (f"; last booked for {profile['last_person']}" if profile.get("last_person") else ""))
    if profile.get("display_name"):
        parts.append(f"name {profile['display_name']}")
    if profile.get("preferred_language"):
        code = profile["preferred_language"]
        parts.append(f"usual language {language_policy.display_name(code)}")
    facts = profile.get("facts") or {}
    if facts:
        parts.append("usual: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in facts.items()))
    booking = profile.get("last_booking")
    line = _bookings_line(profile.get("bookings") or []) or (_upcoming_line(upcoming) if upcoming is not None else "")
    if line:
        parts.append(line)
    elif booking and upcoming is None:     # live store not read: the profile's last booking
        what = _when_for_prompt(booking)
        if booking.get("purpose"):
            what += f" for {booking['purpose']}"
        if what.strip():
            parts.append(f"last booking {what.strip()}")
    if profile.get("last_outcome"):
        parts.append(f"last call {profile['last_outcome']}")
    for f in profile.get("follow_ups") or []:
        parts.append(f"ask again: {f['note']}")
    if not parts:
        return ""
    hint = number_hint(profile.get("number"))
    ask = asked_person(profile)
    who = (ask["name"].split()[0] if ask else "")
    family_rule = (f" When the booking is not yet for someone named, ask one short question in the caller's language, with a "
                   f"respectful form (ji, or aai, aaji, mother, father as they say it): \"Is this for {who} ji again, or for "
                   "someone else?\". Then book for whoever they name, and confirm that person's usual details only as far as "
                   "they help this booking. Never volunteer one person's visits, doctor or details when the caller names "
                   "someone else." if ask else "")
    existing_rule = (" The caller was identified by their caller ID and already has the bookings listed above, so the number is "
                     "known: never ask about the phone number. A booking marked cancelled is gone: never offer it or change it. When they refer to one of these bookings (\"my Monday "
                     "appointment\", \"that booking\", \"मुझे सोमवार की appointment\") it is THAT booking, not a new one: do not "
                     "check availability or start a booking, and do not ask for a number. If they gave no verb, ask in one short "
                     "question what they want to do with it (change the time, cancel, or something else); if they did (cancel, "
                     "shift to 6), act on that booking." if line else "")
    return ("(What we know about this caller from earlier calls: " + "; ".join(parts) + "."
            + (f" The number on this line is {hint}." if hint else "")
            + " Never read it all out. Confirm what you know in one short question instead of asking from scratch (\"Same "
            "number as last time, " + (hint if hint and "x" in hint else "98xxxx3210") + "?\"), and read a booking back before "
            "you make it. The caller may not be the same person: share nothing beyond what helps this call."
            + existing_rule + family_rule + " If they ask to be forgotten, use forget_me.)")


# what a live call knows about its caller: conversation id -> {"block", "owner", "number", "names"} (newest kept)
_CONTEXT: "collections.OrderedDict[str, Dict[str, Any]]" = collections.OrderedDict()
_CONTEXT_MAX = 200


def set_context(conversation_id: Optional[str], profile: Optional[Dict[str, Any]], agent_id: Optional[str] = None) -> None:
    """Remember the block for the conversation (the phone call opened it): every turn's prompt reads it."""
    if not conversation_id or not profile:
        return
    upcoming = None
    if profile.get("number"):
        upcoming = live_bookings(profile["number"], agent_id)     # the live store: a reschedule or cancel is seen
    block = block_text(profile, upcoming)
    if not block:
        return
    with _lock:
        _CONTEXT[str(conversation_id)] = {"block": block, "owner": profile.get("owner", ""), "number": profile.get("number", ""),
                                          "names": names_of(profile)}
        _CONTEXT.move_to_end(str(conversation_id))
        while len(_CONTEXT) > _CONTEXT_MAX:
            _CONTEXT.popitem(last=False)


KNOWN_NAMES_MAX = 4


def names_of(profile: Dict[str, Any]) -> List[str]:
    """The caller's usual name, then the people booked for (the latest first), without repeats, at most KNOWN_NAMES_MAX."""
    out: List[str] = []
    for n in [profile.get("display_name")] + [p.get("name") for p in people_by_recency(profile.get("people") or [])]:
        if isinstance(n, str) and n.strip() and n.strip().lower() not in {o.lower() for o in out}:
            out.append(n.strip())
    return out[:KNOWN_NAMES_MAX]


def known_names(conversation_id: Optional[str]) -> List[str]:
    """The names to spell right in this live call's speech recognition (Gnani Prisma's `speaker_names`): the caller's usual
    name and the people booked for on this number, the most recent first, at most four. Read from what the call opened with
    (`set_context`): a dict lookup, no store read. [] when the call has no known caller, CALLER_PROFILES is off or anything
    goes wrong. Never raises."""
    try:
        if not conversation_id or not enabled():
            return []
        with _lock:
            names = (_CONTEXT.get(str(conversation_id)) or {}).get("names") or []
            return [str(n) for n in names][:KNOWN_NAMES_MAX]
    except Exception:  # noqa: BLE001
        return []


def prompt_block(conversation_id: Optional[str]) -> str:
    """The block for this conversation's turn prompt, or "" (no known caller, or CALLER_PROFILES off). A dict lookup."""
    if not conversation_id or not enabled():
        return ""
    with _lock:
        return (_CONTEXT.get(str(conversation_id)) or {}).get("block", "")


def forget_context(conversation_id: Optional[str] = None) -> None:
    with _lock:
        if conversation_id is None:
            _CONTEXT.clear()
        else:
            _CONTEXT.pop(str(conversation_id), None)


def prefill_form(profile: Optional[Dict[str, Any]], confirmed: bool = False) -> Dict[str, str]:
    """The name and phone the caller details form starts a call with, so the agent does not ask for them again."""
    if not profile:
        return {}
    form: Dict[str, str] = {}
    if profile.get("display_name"):
        form["name"] = profile["display_name"]
    if profile.get("number"):
        phone = caller_memory.clean_value("phone", form_phone(profile["number"]))
        if phone:
            form["phone"] = phone
            if confirmed:      # identified by caller ID and already booked: the number is settled, it is never asked
                form["phone_confirmed"] = "yes"
    return form


# ---------------------------------------------------------------------------------------------------- call end

def _tool_calls(turn: dict) -> List[dict]:
    calls = turn.get("tool_calls")
    return [c for c in calls if isinstance(c, dict)] if isinstance(calls, list) else []


def _same_name(a: Optional[str], b: Optional[str]) -> bool:
    x, y = (a or "").lower().split(), (b or "").lower().split()
    return bool(x and y and (x == y or x[0] == y[0]))


_INTRO = re.compile(r"\bmy name(?:'s|’s| is)\b|\bmera naam\b|\bmain\s+\S+\s+(?:bol|speaking)|\bthis is\b|\bi am\b|\bi'm\b|"
                    r"मेरा नाम|मैं\s+\S+\s+बोल", re.IGNORECASE)


def introduced_as(turns: Sequence[dict], name: Optional[str]) -> bool:
    """Did the caller introduce themselves with `name` ("my name is ...", "mera naam ... hai", "this is ...")? A name that
    appears on a call from a number we know, without that, is the person the booking is for."""
    first = (name or "").split()[0].lower() if name else ""
    return bool(first) and any(
        t.get("role") == "user" and _INTRO.search(str(t.get("text_content") or "")) and first in str(t.get("text_content") or "").lower()
        for t in turns)


# ---------------------------------------------------------------------------------------------------- family relations

_REL_LATIN_WORDS = dict(caller_memory._RELATION_WORDS)
_REL_LATIN_WORDS.update({"mummy": "mother", "mataji": "mother", "pitaji": "father", "aai": "mother", "aaji": "grandmother",
                         "baba": "father", "vadil": "father", "bhabhi": "other", "chacha": "other",
                         "bua": "other", "mama": "other", "mausi": "other"})
# Devanagari stems: Marathi joins its postpositions to the word ("आईसाठी", "वडिलांसाठी"), so a stem is matched as a prefix
_REL_DEVA_STEMS = (
    ("मम्मी", "mother"), ("आई", "mother"), ("माँ", "mother"), ("मां", "mother"), ("माता", "mother"),
    ("पिताजी", "father"), ("वडिल", "father"), ("वडील", "father"), ("बाबा", "father"), ("पापा", "father"), ("पिता", "father"),
    ("मुलगा", "son"), ("मुलाल", "son"), ("मुलास", "son"), ("बेटे", "son"), ("बेटा", "son"),
    ("मुलगी", "daughter"), ("मुलीस", "daughter"), ("मुलील", "daughter"), ("बेटी", "daughter"),
    ("पत्नी", "wife"), ("बायको", "wife"), ("बीवी", "wife"), ("पति", "husband"), ("पती", "husband"), ("नवरा", "husband"),
    ("नवऱ्या", "husband"), ("भाई", "brother"), ("भाऊ", "brother"), ("भावा", "brother"), ("बहन", "sister"), ("बहीण", "sister"),
    ("बहिण", "sister"), ("दादी", "grandmother"), ("नानी", "grandmother"), ("आजी", "grandmother"), ("दादा", "grandfather"),
    ("नाना", "grandfather"), ("आजोबा", "grandfather"),
)
_REL_POSSESSIVE_LATIN = re.compile(r"\b(?:my|mere|meri|mera|apne|apni|apna|majhya|majhi|maza|mazi)\s+(?:old\s+|elderly\s+)?(" +
                                   "|".join(sorted(_REL_LATIN_WORDS, key=len, reverse=True)) + r")\b", re.IGNORECASE)
_REL_POSSESSIVE_DEVA = re.compile(r"(?:मेरे|मेरी|मेरा|माझा|माझी|माझे|माझ्या|अपने|अपनी)\s+(\S+)")
_SELF_WORDS = re.compile(r"\b(?:for myself|for me|myself|mere liye|apne liye|for self)\b|मेरे लिए|माझ्यासाठी", re.IGNORECASE)


def _deva_relation(word: str) -> Optional[str]:
    for stem, rel in _REL_DEVA_STEMS:
        if word.startswith(stem):
            return rel
    return None


def relation_in(text: str) -> Optional[str]:
    """The relation the caller names in one message, in English: "for my mother" and "meri maa ke liye" give "mother", "माझ्या
    आईसाठी" gives "mother", "मेरे पापा के लिए" gives "father", "for myself" gives "self". None when no relation is named. The
    latest one in the message wins (the caller corrects themselves)."""
    text = unicodedata.normalize("NFC", str(text or ""))
    found: List[Tuple[int, str]] = []
    for m in _REL_POSSESSIVE_LATIN.finditer(text):
        found.append((m.start(), _REL_LATIN_WORDS[m.group(1).lower()]))
    for m in _REL_POSSESSIVE_DEVA.finditer(text):
        rel = _deva_relation(m.group(1))
        if rel:
            found.append((m.start(), rel))
    for m in _SELF_WORDS.finditer(text):
        found.append((m.start(), "self"))
    return max(found)[1] if found else None


def relation_of_call(turns: Sequence[dict]) -> Optional[str]:
    """The relation the caller named last on the call (from what they said), or None."""
    for t in reversed(list(turns)):
        if t.get("role") == "user":
            got = relation_in(str(t.get("text_content") or ""))
            if got:
                return got
    return None


def clean_relation(value: Any) -> Optional[str]:
    """A relation word (English, Hindi, Marathi, romanised) as one of the stored English relations, or None."""
    v = unicodedata.normalize("NFC", str(value or "")).strip().lower()
    if not v:
        return None
    if v in _RELATIONS:
        return v
    if v in _REL_LATIN_WORDS:
        return _REL_LATIN_WORDS[v]
    return _deva_relation(v)



def read_call(turns: Sequence[dict], form: Optional[Dict[str, str]], reply_language: Optional[str] = None,
              *, note: Optional[str] = None, known_name: Optional[str] = None,
              schema: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """What one finished call tells us about its caller, composed from the stored turns (oldest first), the caller details
    form and the conversation's remembered language. No model call. None when the caller said nothing (the call ended
    on the greeting): there is nothing to remember.

    `known_name` is the name the number already has: a different name on this call, which the caller did not introduce
    themselves with, is the person the booking was for (a mother ringing for her son), not a new caller. `schema` is the
    agent's `remember` schema (services/caller_memory.py): only its details are kept, each validated by its type.
    Never kept: an exact age (only an age band), an identity number, a card, an OTP, a password or a health condition."""
    spoken = [t for t in turns if t.get("role") == "user" and str(t.get("text_content") or "").strip()
              and t.get("text_content") != "[call connected]"]
    if not spoken:
        return None
    form = form or {}
    booking: Optional[Dict[str, str]] = None
    booked_name: Optional[str] = None
    booked_relation: Optional[str] = None
    cancelled = False
    booked_ok = False
    tool_failed = False
    for t in turns:
        for c in _tool_calls(t):
            name, args = c.get("name"), c.get("arguments") if isinstance(c.get("arguments"), dict) else {}
            if not c.get("ok"):
                tool_failed = True
                continue
            if name in BOOK:
                booking = _clean_booking({"date": args.get("date"), "time": args.get("time"),
                                          "purpose": args.get("reason") or form.get("reason")})
                booked_ok, cancelled = booking is not None, False
                booked_name = _clean("name", args.get("name")) if booked_ok else None
                booked_relation = (args.get("relation") or args.get("booked_by")) if booked_ok else None
            elif name in CANCEL_BOOKING:
                booking, cancelled, booked_ok, booked_name, booked_relation = None, True, False, None, None
    agent_replies = [t for t in turns if t.get("role") == "agent"]
    gap = any(t.get("fallback") or kg.is_gap_reply(str(t.get("text_content") or "")) for t in agent_replies[1:])
    if booked_ok:
        outcome = "booked"
    elif gap or tool_failed:
        outcome = "unanswered"
    else:
        outcome = "asked"
    language = (reply_language or "").strip().lower() or None
    if not language:
        langs = [str(t.get("language") or "").strip().lower() for t in agent_replies[1:]]
        language = next((l for l in reversed(langs) if l), None)
    name = _clean("name", form.get("name"))
    person = _clean("name", form.get("person"))
    relation = clean_relation(form.get("relation"))
    if known_name and name and not _same_name(name, known_name) and not introduced_as(turns, name):
        person = person or name          # a different name on a known number: who the booking is for
        name = known_name
    if person and name and _same_name(person, name) and person.lower() == name.lower():
        person, relation = None, None
    # who the booking was for, and how they are related to the caller: what the booking tool was given (`relation` or
    # `booked_by`), what the caller said ("for my mother", "माझ्या आईसाठी"), else what the form holds
    told = clean_relation(booked_relation)
    said = relation_of_call(turns)
    named = told or (said if said != "self" else None) or relation
    for_whom = booked_name or person or None
    if for_whom and named and named != "self" and name and _same_name(for_whom, name) and not introduced_as(turns, name):
        # "for my mother": the name on the booking is the patient's, so it is not the caller's own name
        name = known_name if known_name and not _same_name(known_name, for_whom) else None
    relation = named or (said if said == "self" else None) or relation
    # the people of this call: who the booking was made for (named by the caller and/or by the tool) and who else was named
    entries: List[Dict[str, Any]] = []
    band = None
    spec_band = caller_memory.facts_of(form).get("age_band")
    if spec_band in caller_memory.AGE_BANDS:
        band = spec_band
    own = bool(for_whom and (relation == "self" or (name and _same_name(for_whom, name) and relation is None)))
    if for_whom:
        entries.append({"name": for_whom, "relation": "self" if own else (relation if relation != "self" else None),
                        "age_band": band, "booked": booked_ok, "booking": booking if booked_ok else None, "own": own})
    if person and not any(_same_name(person, e["name"]) for e in entries):
        entries.append({"name": person, "relation": relation if relation != "self" else None, "age_band": band,
                        "booked": False, "own": False})
    # the trade details: those of the agent's schema, validated again; the age band belongs to the person
    facts: Dict[str, str] = {}
    for key, value in caller_memory.facts_of(form).items():
        spec = caller_memory.spec_of(schema, key) if schema else {"key": key, "type": "text", "max": 100}
        if spec is None or key == "age_band" or spec.get("rule") == "age_band":
            continue
        v = caller_memory.validate_fact(spec, value)
        if v:
            facts[key] = v
    bits: List[str] = []
    if booked_ok and booking:
        who = f" for {for_whom}" if for_whom and not _same_name(for_whom, name) else ""
        bits.append("booked " + _when_for_prompt(booking).strip() + (f" for {booking['purpose']}" if booking.get("purpose") else "") + who)
    elif cancelled:
        bits.append("cancelled their booking")
    elif form.get("reason") and caller_memory.safe_text(form["reason"]):
        bits.append(f"called about {caller_memory.safe_text(form['reason'])}")
    if form.get("doctor") and (booked_ok or cancelled):
        bits.append(f"with {form['doctor']}")
    if outcome == "unanswered":
        bits.append("the agent could not answer something")
    if form.get("other"):
        bits.append(form["other"])
    summary = ("; ".join(b for b in bits if b.strip()) or "asked a question and got an answer")
    return {"display_name": name, "preferred_language": language, "last_outcome": outcome, "booking": booking,
            "cancelled": cancelled, "summary": summary[:SUMMARY_MAX], "facts": facts, "people": entries,
            "person_facts": facts if any(e.get("booked") and not e.get("own") for e in entries) else {}}


def _same_person(a: str, b: str) -> bool:
    """Is `b` the person `a` names more or less completely ("Sunita" and "Sunita Kulkarni")? One name's words are all in the other's."""
    x, y = set(a.lower().split()), set(b.lower().split())
    return bool(x and y and (x <= y or y <= x))


def _merge_people(old: Sequence[Dict[str, Any]], entries: Sequence[Dict[str, Any]], now: str,
                  person_facts: Optional[Dict[str, str]] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """(people after the call, the person the last booking was for or None when this call booked nobody). A person booked for
    moves to the end of the list (the list is oldest first, so the newest is last); their usual details (`person_facts`, the
    trade's details of a booking made for somebody else) and their last booking are kept with them."""
    people = [dict(p) for p in old]
    last: Optional[str] = None
    for e in entries:
        name = _clean("name", e.get("name"), 60)
        if not name:
            continue
        found = next((p for p in people if p["name"].lower() == name.lower()), None) or \
            next((p for p in people if _same_person(p["name"], name)), None)
        if found is None:
            found = {"name": name}
            people.append(found)
        elif len(name.split()) > len(found["name"].split()):
            found["name"] = name         # "Sunita" is now known as "Sunita Kulkarni"
        rel = e.get("relation")
        if rel in _RELATIONS and not (rel == "other" and found.get("relation")):
            found["relation"] = rel
        if e.get("age_band") in caller_memory.AGE_BANDS:
            found["age_band"] = e["age_band"]
        if e.get("booked"):
            found["last_booked_at"] = now
            if e.get("booking"):
                found["last_booking"] = dict(e["booking"])
            if person_facts and not e.get("own"):
                merged = dict(found.get("facts") or {})
                for k, v in person_facts.items():
                    merged.pop(k, None)
                    merged[k] = v
                found["facts"] = dict(list(merged.items())[-FACTS_MAX:])
            people.remove(found)
            people.append(found)
            last = found["name"]
    return people[-PEOPLE_MAX:], last


def merge_call(existing: Optional[Dict[str, Any]], owner: str, number: str, update: Dict[str, Any], *,
               conversation_id: Optional[str], returning: bool, follow_ups: Sequence[str] = (),
               when: Optional[str] = None, source: str = "phone") -> Dict[str, Any]:
    """The profile after a call: `update` (read_call) on top of the existing one. Follow-ups of earlier calls were put to the
    agent on this call and are dropped; the ones set on this call replace them. Details learned on this call replace the
    same details from earlier calls and leave the others. `source` is where the call came from: a number first met on a web
    call is marked "web call" (the caller typed or said it, nobody verified it) until a phone call comes from that number."""
    now = when or _now_iso()
    old = clean_profile(existing) if existing else {}
    p = dict(old) if old else {"created_at": now, "call_count": 0, "calls": []}
    p.update({"id": profile_id(owner, number), "owner": owner, "number": number})
    if update.get("display_name"):
        p["display_name"] = update["display_name"]
    if update.get("preferred_language"):
        p["preferred_language"] = update["preferred_language"]
    p["last_call_at"] = now
    p["last_outcome"] = update.get("last_outcome")
    if update.get("booking"):
        p["last_booking"] = update["booking"]
    elif update.get("cancelled"):
        p["last_booking"] = None
    p["follow_ups"] = [{"note": n, "at": now} for n in follow_ups]
    p["summary"] = update.get("summary")
    facts = dict(old.get("facts") or {}) if old else {}
    for k, v in ({} if update.get("person_facts") else (update.get("facts") or {})).items():   # a booking for somebody else: the details are theirs
        facts.pop(k, None)
        facts[k] = v
    p["facts"] = dict(list(facts.items())[-FACTS_MAX:])
    p["people"], last_person = _merge_people(old.get("people") or [], update.get("people") or [], now,
                                             update.get("person_facts"))
    if last_person:
        p["last_person"] = last_person
    p["source"] = "phone" if source == "phone" or old.get("source") == "phone" else (old.get("source") or source)
    if returning and old and (old.get("display_name") or old.get("last_booking")):
        p["notice_given"] = True        # the greeting said that the details are kept (services/phone_greeting.py)
    p["call_count"] = int(old.get("call_count") or 0) + 1 if old else 1
    calls = list(old.get("calls") or []) if old else []
    if conversation_id:
        calls.append({"conversation_id": str(conversation_id), "at": now, "returning": bool(returning)})
    p["calls"] = calls[-MAX_CALLS:]
    p["updated_at"] = now
    return clean_profile(p)


@_atomic
def record_call(owner: str, number: Optional[str], mode: str, update: Dict[str, Any], *, conversation_id: Optional[str],
                returning: bool, follow_ups: Sequence[str] = (), source: str = "phone") -> Optional[Dict[str, Any]]:
    """Read the existing profile, apply the call, write it back. Blocking; never raises. Returns the profile."""
    n = number_of(number)
    if not enabled() or not n or not owner:
        return None
    try:
        store = get_store(mode)
        raw = store.get(owner, n)
        p = merge_call(raw if isinstance(raw, dict) else None, owner, n, update, conversation_id=conversation_id,
                       returning=returning, follow_ups=follow_ups, source=source)
        p = sync_bookings(p)      # the call may have moved or cancelled a booking: say so on the profile
        store.put(p)
        log.info("caller profile updated (%s, %d calls, outcome %s, source %s)", mask_number(n), p["call_count"],
                 p["last_outcome"], p["source"])
        return p
    except Exception as e:  # noqa: BLE001 - a profile that cannot be written must not break anything
        log.warning("could not write a caller profile: %s", e.__class__.__name__)
        return None


def load_conversation(conversation_id: str, user_id: str, *, local_db: Any = None, supabase: Any = None
                      ) -> Tuple[List[dict], Optional[str]]:
    """(turns oldest first, remembered reply language) of a finished conversation. Blocking; ([], None) on any failure."""
    try:
        if local_db is not None:
            turns = local_db.select("conversation_turns", lambda r: r.get("conversation_id") == conversation_id,
                                    order="created_at")
            conv = local_db.get("conversations", conversation_id, user_id) or {}
            return list(turns), conv.get("reply_language")
        if supabase is not None:
            cols = ("role, text_content, tool_calls, fallback, language", "role, text_content, fallback, language",
                    "role, text_content")
            turns = []
            for c in cols:
                try:
                    turns = (supabase.table("conversation_turns").select(c).eq("conversation_id", conversation_id)
                             .order("created_at").execute().data or [])
                    break
                except Exception:  # noqa: BLE001 - a migration may not be applied: fewer columns
                    continue
            language = None
            try:
                rows = (supabase.table("conversations").select("reply_language").eq("id", conversation_id).limit(1)
                        .execute().data or [])
                language = rows[0].get("reply_language") if rows else None
            except Exception:  # noqa: BLE001 - migration 014 may not be applied
                pass
            return list(turns), language
    except Exception as e:  # noqa: BLE001
        log.warning("could not read the finished call for its caller profile: %s", e.__class__.__name__)
    return [], None


def _agent_of(conversation_id: str, user_id: str, *, local_db: Any = None, supabase: Any = None) -> Optional[dict]:
    """The agent row a conversation belongs to (for its template's `remember` schema). Blocking; None on any failure."""
    try:
        if local_db is not None:
            conv = local_db.get("conversations", conversation_id, user_id) or {}
            return local_db.get("agents", conv.get("agent_id"), user_id) if conv.get("agent_id") else None
        if supabase is not None:
            rows = (supabase.table("conversations").select("agent_id").eq("id", conversation_id).limit(1).execute().data or [])
            if not rows:
                return None
            ag = supabase.table("agents").select("template_id").eq("id", rows[0]["agent_id"]).limit(1).execute().data or []
            return ag[0] if ag else None
    except Exception as e:  # noqa: BLE001
        log.debug("could not read the call's agent: %s", e.__class__.__name__)
    return None


def finish_call(*, owner: str, number: Optional[str], mode: str, conversation_id: Optional[str], returning: bool,
                follow_ups: Sequence[str] = (), local_db: Any = None, supabase: Any = None, source: str = "phone",
                agent: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """Everything done when a call is over: read the conversation, compose the profile update, write it. Blocking. Nothing is
    written for a caller who asked to be forgotten on this call, or when the caller said nothing."""
    n = number_of(number)
    if not enabled() or not conversation_id or not n or is_forgotten(conversation_id):
        return None
    turns, language = load_conversation(conversation_id, owner, local_db=local_db, supabase=supabase)
    form = caller_memory.load(conversation_id, local_db, owner)
    agent = agent or _agent_of(conversation_id, owner, local_db=local_db, supabase=supabase)
    try:
        existing = lookup_any_age(owner, n, mode)
    except Exception:  # noqa: BLE001
        existing = None
    update = read_call(turns, form, language, known_name=(existing or {}).get("display_name"),
                       schema=caller_memory.schema_for(agent))
    if update is None:
        return None
    with _lock:
        gone = set(_FORGOTTEN_PEOPLE.pop(str(conversation_id), set()))
    if gone:      # asked to forget one person on this call: they are not written back by the same call
        update["people"] = [e for e in update.get("people") or [] if str(e.get("name") or "").lower() not in gone]
    return record_call(owner, n, mode, update, conversation_id=conversation_id, returning=returning,
                       follow_ups=follow_ups, source=source)


def on_phone_call_end(*, owner: str, number: Optional[str], mode: str, conversation_id: Optional[str], returning: bool,
                      follow_ups: Sequence[str] = (), local_db: Any = None, supabase: Any = None,
                      delay: Optional[float] = None, source: str = "phone", agent: Optional[dict] = None) -> bool:
    """A phone call is over (routers/phone.py, `finally` of the call). Writes the profile on a background thread after
    CALLER_PROFILE_END_DELAY_S so the last turn and the small model's last reading are in the store. Never blocks, never
    raises. Returns whether a write was queued. `number` is the caller's for an inbound call and the dialled number for an
    outbound one (a callback to a missed call, a reminder): the profile is keyed by the other party either way."""
    try:
        if not enabled() or not conversation_id or not number_of(number):
            return False
        wait = end_delay_seconds() if delay is None else delay
        notes = list(follow_ups)

        def run() -> None:
            try:
                finish_call(owner=owner, number=number, mode=mode, conversation_id=conversation_id, returning=returning,
                            follow_ups=notes, local_db=local_db, supabase=supabase, source=source, agent=agent)
            except Exception as e:  # noqa: BLE001
                log.warning("caller profile write failed: %s", e.__class__.__name__)
            finally:
                forget_context(conversation_id)

        timer = threading.Timer(wait, run)
        timer.daemon = True
        timer.start()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("could not queue a caller profile write: %s", e.__class__.__name__)
        return False


def on_web_call_end(*, owner: str, mode: str, conversation_id: Optional[str], local_db: Any = None, supabase: Any = None,
                    delay: Optional[float] = None, agent: Optional[dict] = None) -> bool:
    """A browser web call is over (routers/web_call.py: the visitor hung up). When the caller gave a phone number during the
    call, the call is filed under that number (normalised to E.164) with the source "web call". A call in which no number
    was given writes nothing: there is nothing to key the profile by. Background thread, never raises."""
    try:
        if not enabled() or not conversation_id:
            return False
        form = caller_memory.load(conversation_id, local_db, owner)
        number = number_of(form.get("phone"))
        if not number:
            return False
        return on_phone_call_end(owner=owner, number=number, mode=mode, conversation_id=conversation_id, returning=False,
                                 local_db=local_db, supabase=supabase, delay=delay, source="web call", agent=agent)
    except Exception as e:  # noqa: BLE001
        log.warning("could not queue a web call profile write: %s", e.__class__.__name__)
        return False


# ---------------------------------------------------------------------------------------------------- forget me

# conversations whose caller asked to be forgotten: nothing of them is written when the call ends
_FORGOTTEN: "collections.OrderedDict[str, bool]" = collections.OrderedDict()
_FORGOTTEN_PEOPLE: "collections.OrderedDict[str, set]" = collections.OrderedDict()   # conversation -> names asked to be forgotten
_PENDING_FORGET: "collections.OrderedDict[str, int]" = collections.OrderedDict()   # conversation -> history length at the read-back
_FORGET_MAX = 500

FORGET_TOOL = {
    "tool": "forget_me",
    "description": ("The caller asks you to forget them or delete what you remember about them (\"forget me\", \"mera data "
                    "hata do\", \"माझी माहिती काढून टाका\"). Step 1: call it with confirmed false; it tells you what to read "
                    "back. Read back, in the caller's language, that you will delete the name, details and notes you keep "
                    "for this number, and ask \"Shall I delete it?\". Step 2: only after the caller says yes in their next "
                    "message, call it again with confirmed true. Never delete without that yes. When the caller asks to "
                    "forget only one person (\"forget my father's details\", \"मेरी माँ की जानकारी हटाओ\"), pass `person` "
                    "(the name or the relation as they said it): only that person is deleted, and the rest stays."),
    "schema": {"type": "object", "properties": {"confirmed": {"type": "boolean"}, "person": {"type": "string", "maxLength": 60}},
               "additionalProperties": False},
}


def mark_forgotten(conversation_id: Optional[str]) -> None:
    if not conversation_id:
        return
    with _lock:
        _FORGOTTEN[str(conversation_id)] = True
        while len(_FORGOTTEN) > _FORGET_MAX:
            _FORGOTTEN.popitem(last=False)
    caller_memory.suppress(str(conversation_id))


def is_forgotten(conversation_id: Optional[str]) -> bool:
    with _lock:
        return str(conversation_id) in _FORGOTTEN


def _forget_person(cid: str, who: str, confirmed: bool, run: Any) -> Tuple[bool, Any]:
    """`forget_me` for one person of the number ("forget my father's details"): the same two steps, the same yes, but only that
    person is removed; the caller's profile, the rest of the family and this call stay."""
    ctx = dict(_CONTEXT.get(cid) or {})
    owner, number = ctx.get("owner") or getattr(run, "user_id", ""), ctx.get("number")
    mode = getattr(run, "storage_mode", "local") or "local"
    if not number or getattr(run, "source", None) not in ("phone", "missed_call"):
        return False, ("NOT DONE: there is nobody on file for this call to remove a person from. Tell the caller, in their "
                       "language, that nothing is kept about that person.")
    try:
        prof = lookup_any_age(owner, number, mode)
    except Exception:  # noqa: BLE001
        prof = None
    person = find_person(prof, who) if prof else None
    if person is None:
        names = ", ".join(x["name"] for x in (prof or {}).get("people") or [])
        return False, ("NOT DONE: no single person on this number matches \"" + who[:40] + "\"" +
                       (f" (kept: {names})" if names else "") + ". Ask the caller, in their language, whose details they mean.")
    key = f"{cid}|{person['name'].lower()}"
    rows = len(getattr(run, "history", None) or [])
    with _lock:
        asked_at = _PENDING_FORGET.get(key)
        if not confirmed or asked_at is None or rows <= asked_at:
            _PENDING_FORGET[key] = rows if (not confirmed or asked_at is None) else asked_at
            _PENDING_FORGET.move_to_end(key)
            while len(_PENDING_FORGET) > _FORGET_MAX:
                _PENDING_FORGET.popitem(last=False)
            return False, (f"NOT DONE: first tell the caller, in their language, that you will delete everything you keep "
                           f"about {person['name']} (name, details, last booking) and nobody else, and ask \"Shall I delete "
                           "it?\". Call this tool again with confirmed true and the same person only after they say yes in "
                           "their next message.")
        _PENDING_FORGET.pop(key, None)
        _FORGOTTEN_PEOPLE.setdefault(cid, set()).add(person["name"].lower())
        while len(_FORGOTTEN_PEOPLE) > _FORGET_MAX:
            _FORGOTTEN_PEOPLE.popitem(last=False)
    done = None
    try:
        done = remove_person(owner, number, person["name"], mode)
    except Exception as e:  # noqa: BLE001
        log.warning("could not delete a person of a caller profile on request: %s", e.__class__.__name__)
    log.info("caller asked to forget one person (%s)", "deleted" if done else "none to delete")
    return True, {"message": f"Deleted. Nothing about {person['name']} is kept, and nothing about them from this call will be. "
                             "Everyone else stays. Tell the caller that, in their language."}


def forget_run(tool: str, args: dict, run: Any) -> Tuple[bool, Any]:
    """Blocking (the tool thread): the `forget_me` tool. The first call, and any call before the caller has answered in a
    later turn, only says what to read back; the confirmed call deletes the number's profile (phone calls only: a web
    visitor's number is not verified, so a web call just stops anything of it being kept), stops this call from writing one
    and clears what the call has collected."""
    cid = str(getattr(run, "conversation_id", "") or "")
    if not cid:
        return False, "there is nothing to forget on this call"
    who = str((args or {}).get("person") or "").strip()
    if who:
        return _forget_person(cid, who, bool((args or {}).get("confirmed")), run)
    confirmed = bool((args or {}).get("confirmed"))
    rows = len(getattr(run, "history", None) or [])
    with _lock:
        asked_at = _PENDING_FORGET.get(cid)
        if not confirmed or asked_at is None or rows <= asked_at:
            _PENDING_FORGET[cid] = rows if (not confirmed or asked_at is None) else asked_at
            _PENDING_FORGET.move_to_end(cid)
            while len(_PENDING_FORGET) > _FORGET_MAX:
                _PENDING_FORGET.popitem(last=False)
            return False, ("NOT DONE: first tell the caller, in their language, that you will delete everything you keep for "
                           "their number (name, details, notes) and nothing from this call will be kept, and ask \"Shall I "
                           "delete it?\". Call this tool again with confirmed true only after they say yes in their next "
                           "message.")
        ctx = dict(_CONTEXT.get(cid) or {})
        _PENDING_FORGET.pop(cid, None)
    owner, number = ctx.get("owner") or getattr(run, "user_id", ""), ctx.get("number")
    mark_forgotten(cid)
    deleted = False
    if number and getattr(run, "source", None) in ("phone", "missed_call"):
        try:
            deleted = delete(owner, number, getattr(run, "storage_mode", "local") or "local")
        except Exception as e:  # noqa: BLE001
            log.warning("could not delete a caller profile on request: %s", e.__class__.__name__)
    forget_context(cid)
    log.info("caller asked to be forgotten (profile %s)", "deleted" if deleted else "none to delete")
    return True, {"message": ("Deleted. Everything kept for this number is gone and nothing from this call will be kept. "
                              "Tell the caller that, in their language." if deleted else
                              "Done. Nothing from this call will be kept. Tell the caller that, in their language.")}


def forget_specs(run: Any, tools_mod: Any) -> List[Any]:
    """The `forget_me` tool spec for this turn (empty with CALLER_PROFILES off), run in this process."""
    if not enabled():
        return []
    spec = tools_mod.ToolSpec("call", FORGET_TOOL["tool"], FORGET_TOOL["description"], FORGET_TOOL["schema"])
    spec.runner = lambda tool, args, _run=run: forget_run(tool, args, _run)
    return [spec]


# ---------------------------------------------------------------------------------------------------- the owner edits

class ProfileEditError(ValueError):
    """The owner's edit holds something Vakya does not keep (an identity number, a card, an OTP, a password, a health
    condition) or something malformed."""


@_atomic
def update_profile(owner: str, key: str, patch: Dict[str, Any], mode: str = "local") -> Optional[Dict[str, Any]]:
    """Apply the owner's edit to a caller's profile and return it (None when there is none). `patch` may hold display_name,
    preferred_language, facts ({key: value, or None to remove}), people ([{name, relation, age_band}]: the whole list),
    last_person and add_note (one more "ask next time" note). Every value goes through the same cleaning as what a call
    writes; a value that would be dropped raises ProfileEditError instead of vanishing silently. Blocking."""
    p = find(owner, key, mode)
    if p is None:
        return None

    def need(key_: str, value: Any, limit: int, cleaned: Optional[str]) -> str:
        if cleaned is None:
            raise ProfileEditError(f"{key_}: Vakya does not keep identity numbers, card numbers, OTPs, passwords or health "
                                   "details, and values must be short plain text.")
        return cleaned
    if "display_name" in patch:
        v = patch["display_name"]
        p["display_name"] = None if v in (None, "") else need("name", v, 60, _clean("name", v, 60))
    if "preferred_language" in patch:
        v = str(patch["preferred_language"] or "").strip().lower()
        if v and not re.fullmatch(r"[a-z]{2,8}", v):
            raise ProfileEditError("preferred_language: use a language code such as hi, mr or en.")
        p["preferred_language"] = v or None
    if isinstance(patch.get("facts"), dict):
        facts = dict(p.get("facts") or {})
        for k, v in patch["facts"].items():
            if not caller_memory._FACT_KEY.fullmatch(str(k)):
                raise ProfileEditError(f"{k}: a detail's name is lowercase letters, digits and underscores.")
            if v in (None, ""):
                facts.pop(str(k), None)
                continue
            val = need(str(k), v, 100, _clean("other", v, 100))
            if re.search(r"\d{9,}", re.sub(r"[ -]", "", val)):
                raise ProfileEditError(f"{k}: long numbers are not kept.")
            facts.pop(str(k), None)
            facts[str(k)] = val
        if len(facts) > FACTS_MAX:
            raise ProfileEditError(f"At most {FACTS_MAX} details are kept per caller.")
        p["facts"] = facts
    if isinstance(patch.get("people"), list):
        for item in patch["people"]:
            if isinstance(item, dict):
                person = need("person", item.get("name"), 60, _clean("name", item.get("name"), 60))
                if any(c.isdigit() for c in person):
                    raise ProfileEditError("person: a name has no digits.")
        old = {x["name"].lower(): x for x in p.get("people") or []}
        people = _clean_people(patch["people"])
        for person in people:
            before = old.get(person["name"].lower()) or {}
            for k in ("last_booked_at", "last_booking", "facts"):
                if before.get(k) and k not in person:
                    person[k] = before[k]
        p["people"] = people
    if "last_person" in patch:
        p["last_person"] = patch["last_person"]
    if patch.get("add_note"):
        note = need("note", patch["add_note"], NOTE_MAX, _clean("other", patch["add_note"], NOTE_MAX))
        notes = list(p.get("follow_ups") or [])
        if len(notes) >= MAX_FOLLOW_UPS:
            raise ProfileEditError(f"At most {MAX_FOLLOW_UPS} notes for the next call: remove one first.")
        notes.append({"note": note, "at": _now_iso()})
        p["follow_ups"] = notes
    if isinstance(patch.get("follow_ups"), list):       # the owner removes notes by sending the list that stays
        p["follow_ups"] = _clean_follow_ups(patch["follow_ups"])
    p["updated_at"] = _now_iso()
    out = clean_profile(p)
    out["id"] = p.get("id") or profile_id(owner, out["number"])
    get_store(mode).put(out)
    return out


# ---------------------------------------------------------------------------------------------------- one person of the family

_PERSON_NOISE = re.compile(r"\b(?:my|the|his|her|their|details|data|information|info|about|of|ji|ka|ki|ke|ke liye|ko)\b|'s|’s|"
                           r"[.,;!?]", re.IGNORECASE)


def find_person(profile: Dict[str, Any], who: Any) -> Optional[Dict[str, Any]]:
    """The person of this number a caller or the owner means by `who`: a name (full or first, any case) or a relation ("my
    father", "आई", "मेरी माँ"). None when nobody matches or two people do (a mother and her son named alike are not guessed)."""
    people = profile.get("people") or []
    text = unicodedata.normalize("NFC", str(who or "")).strip().lower()
    if not text or not people:
        return None
    cleaned = " ".join(_PERSON_NOISE.sub(" ", text).split())
    for needle in dict.fromkeys(x for x in (text, cleaned) if x):
        exact = [p for p in people if p["name"].lower() == needle]
        if len(exact) == 1:
            return exact[0]
        first = [p for p in people if p["name"].lower().split()[0] == needle.split()[0]] if needle.split() else []
        if len(first) == 1:
            return first[0]
    rel = clean_relation(cleaned) or relation_in(text) or next((clean_relation(w) for w in cleaned.split() if clean_relation(w)), None)
    if rel:
        hits = [p for p in people if p.get("relation") == rel]
        if len(hits) == 1:
            return hits[0]
    return None


@_atomic
def edit_person(owner: str, key: str, name: str, patch: Dict[str, Any], mode: str = "local") -> Optional[Dict[str, Any]]:
    """The owner edits one person of a number: `patch` may hold name (rename), relation, age_band, facts ({key: value, or None
    to remove}). Their last booking stays. Raises ProfileEditError for a value Vakya does not keep; None when the caller or the
    person is not there. Blocking."""
    prof = find(owner, key, mode)
    if prof is None:
        return None
    person = next((x for x in prof.get("people") or [] if x["name"].lower() == str(name or "").strip().lower()), None)
    if person is None:
        return None
    old_name = person["name"]
    if "name" in patch:
        new = _clean("name", patch["name"], 60)
        if not new or any(c.isdigit() for c in new):
            raise ProfileEditError("name: a name is plain letters with no digits, and Vakya does not keep identity numbers or health details.")
        if any(x is not person and x["name"].lower() == new.lower() for x in prof["people"]):
            raise ProfileEditError("name: that name is already on this number.")
        person["name"] = new
    if "relation" in patch:
        raw = patch["relation"]
        if raw in (None, ""):
            person.pop("relation", None)
        else:
            rel = clean_relation(raw)
            if rel is None:
                raise ProfileEditError("relation: use mother, father, son, daughter, wife, husband, brother, sister, "
                                       "grandmother, grandfather, friend, self or other.")
            person["relation"] = rel
    if "age_band" in patch:
        band = str(patch["age_band"] or "").strip().lower()
        if band and band not in caller_memory.AGE_BANDS:
            raise ProfileEditError("age_band: use child, teen, adult or senior.")
        if band:
            person["age_band"] = band
        else:
            person.pop("age_band", None)
    if isinstance(patch.get("facts"), dict):
        facts = dict(person.get("facts") or {})
        for k, v in patch["facts"].items():
            if not caller_memory._FACT_KEY.fullmatch(str(k)):
                raise ProfileEditError(f"{k}: a detail's name is lowercase letters, digits and underscores.")
            if v in (None, ""):
                facts.pop(str(k), None)
                continue
            val = _clean("other", v, 100)
            if val is None or re.search(r"\d{9,}", re.sub(r"[ -]", "", val)):
                raise ProfileEditError(f"{k}: Vakya does not keep identity numbers, card numbers, OTPs, passwords or health details.")
            facts.pop(str(k), None)
            facts[str(k)] = val
        if len(facts) > FACTS_MAX:
            raise ProfileEditError(f"At most {FACTS_MAX} details are kept per person.")
        if facts:
            person["facts"] = facts
        else:
            person.pop("facts", None)
    if prof.get("last_person") == old_name:
        prof["last_person"] = person["name"]
    prof["updated_at"] = _now_iso()
    out = clean_profile(prof)
    out["id"] = prof.get("id") or profile_id(owner, out["number"])
    get_store(mode).put(out)
    return out


@_atomic
def remove_person(owner: str, key: str, name: str, mode: str = "local") -> Optional[Dict[str, Any]]:
    """Forget one person of a number (their name, relation, details and last booking); the caller and the rest of the family
    stay. None when the caller or the person is not there. A live call stops mentioning them. Blocking."""
    prof = find(owner, key, mode)
    if prof is None:
        return None
    gone = str(name or "").strip().lower()
    kept = [x for x in prof.get("people") or [] if x["name"].lower() != gone]
    if len(kept) == len(prof.get("people") or []):
        return None
    prof["people"] = kept
    if (prof.get("last_person") or "").lower() == gone:
        prof["last_person"] = None
    prof["updated_at"] = _now_iso()
    out = clean_profile(prof)
    out["id"] = prof.get("id") or profile_id(owner, out["number"])
    get_store(mode).put(out)
    with _lock:
        for cid, ctx in list(_CONTEXT.items()):
            if ctx.get("owner") == owner and ctx.get("number") == out["number"]:
                _CONTEXT.pop(cid, None)
                set_context(cid, out)
    return out


# ---------------------------------------------------------------------------------------------------- the agent's note

NOTE_TOOL = {
    "tool": "note_for_next_call",
    "description": ("Leave a short note to ask this caller about again the next time they call (something they could not "
                    "decide, a document they need to bring, a question you could not answer yet). Use it rarely, only when "
                    "a follow-up would really help them. Say nothing about the note to the caller."),
    "schema": {"type": "object", "properties": {"note": {"type": "string", "maxLength": NOTE_MAX}},
               "required": ["note"], "additionalProperties": False},
}


def clean_note(value: Any) -> Optional[str]:
    return _clean("other", value, NOTE_MAX)
