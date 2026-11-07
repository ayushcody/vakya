"""One WhatsApp message after a call about what happened to a booking: booked, moved or cancelled.

The agent's tools (and the desk) call `booking_changed()` whenever a booking row changes. Nothing is sent then. The change
waits in an outbox (a JSON file, VAKYA_WHATSAPP_FILE, default backend/data/whatsapp_outbox.json) and is sent once, shortly
after the call ends, showing the *final* state:

* Collapse. Changes to one booking on one call fold into one message: book 3 pm then move to 4 pm is one "Booked ... 4:00 pm";
  book then cancel on the same call is nothing (the caller never held a confirmation), unless a message about that booking was
  already sent (a backstop firing mid-call), then one "Cancelled"; move then move is one "Moved" from the original time to the
  last; move then cancel is "Cancelled" for the original time.
* One message per change, never two. Each message has an idempotency key `booking_id:version:kind`; a key already used is
  never sent again, so a duplicate hang-up, a retry or a restart cannot double-send.
* When. A call's end (the Vobiz hangup callback or the media stream closing, whichever comes first; the web call's end)
  calls `call_ended()`, and the message goes WHATSAPP_SEND_DELAY_S later (default 30). If a call never reports its end, the
  message goes WHATSAPP_BACKSTOP_S (default 300) after the last change. A desk change (source "staff") goes after the same
  short delay with no call. At start-up `resume()` re-arms what a restart left pending.
* Consent. Meta requires opt-in. The first time a number's booking changes on a call, `booking_changed` answers
  {"status": "ask"}; the agent asks "Shall I send the confirmation on WhatsApp?" and reports the answer through
  `record_consent()`. The answer is stored per number (consent.agreed, time, conversation id) and never asked again. A message
  is sent only if consent is "agreed" at send time; a desk change sends only if consent was already given.
* Not set up. Without WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID the exact message is still built and stored, labelled
  "preview" (not sent), so the desk can show it. WHATSAPP_PREVIEW=off turns that off (then an unconfigured setup is "off").

Binding a change to its call: the live call's conversation id is found, in order, from (1) `bind_call(conversation_id)`, a
contextvar a caller may set around a tool run, (2) booking["conversation_id"] (the stored row carries it), (3) the only live
phone call of the booking's front desk (services/call_control.py). With none of these (a web call whose row has no
conversation id) the change is held to the backstop time.

Template texts (each in en, hi, mr) are in TEXTS below and in docs/WHATSAPP.md, which a test keeps identical."""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from services import whatsapp
except ImportError:  # pragma: no cover
    from backend.services import whatsapp

log = logging.getLogger("vakya.booking_notify")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANGUAGES = ("en", "hi", "mr")
KINDS = whatsapp.KINDS
MAX_LOG = 200
ASK_FRESH_S = 1800

_lock = threading.RLock()
_timers: Dict[str, threading.Timer] = {}
_ended: Dict[str, float] = {}
_call: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("vakya_whatsapp_call", default=None)


# ------------------------------------------------------------------------------------------------ settings

def _float(name: str, default: float) -> float:
    try:
        v = float(os.getenv(name, "").strip() or default)
        return v if v >= 0 else default
    except ValueError:
        return default


def send_delay_s() -> float:
    return _float("WHATSAPP_SEND_DELAY_S", 30.0)


def backstop_s() -> float:
    return _float("WHATSAPP_BACKSTOP_S", 300.0)


def _flag(name: str, default: bool = True) -> bool:
    v = os.getenv(name, "").strip().lower()
    return default if not v else v not in ("off", "0", "false", "no")


def preview_on() -> bool:
    return _flag("WHATSAPP_PREVIEW", True)


def enabled() -> bool:
    """WhatsApp confirmations are wanted (WHATSAPP_CONFIRMATIONS, default on) and either set up or in preview mode."""
    return _flag("WHATSAPP_CONFIRMATIONS", True) and (whatsapp.configured() or preview_on())


def file_path() -> str:
    return os.getenv("VAKYA_WHATSAPP_FILE", "").strip() or os.path.join(BACKEND_DIR, "data", "whatsapp_outbox.json")


# ------------------------------------------------------------------------------------------------ storage

def _load() -> dict:
    try:
        with open(file_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for k, v in (("consent", {}), ("asks", {}), ("items", {}), ("sent_keys", {}), ("messages", [])):
        if not isinstance(data.get(k), type(v)):
            data[k] = type(v)()
    return data


def _save(data: dict) -> None:
    path = file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data["messages"] = data["messages"][-MAX_LOG:]
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _digits(value: Any) -> Optional[str]:
    d = re.sub(r"\D", "", str(value or ""))
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return d if len(d) == 10 else None


def mask(phone: str) -> str:
    return "••••" + phone[-4:]


def _now() -> float:
    return time.time()


def _iso(epoch: Optional[float] = None) -> str:
    return datetime.fromtimestamp(epoch if epoch is not None else _now()).astimezone().isoformat(timespec="seconds")


# ------------------------------------------------------------------------------------------------ the call

def bind_call(conversation_id: Optional[str]) -> "contextvars.Token":
    """Make `conversation_id` the current call for this context (the thread that runs a tool; services/turn_pipeline.py
    binds every scheduling tool to its call). Returns the token for `_call.reset`."""
    return _call.set(str(conversation_id) if conversation_id else None)


def current_call_id() -> Optional[str]:
    return _call.get()


def _live_call_ids(agent_id: Optional[str] = None) -> List[str]:
    try:
        try:
            from services import call_control  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            from backend.services import call_control  # noqa: PLC0415
        out = []
        for s in call_control.live_sessions():
            cid = getattr(s, "conversation_id", None)
            if cid and (agent_id is None or (getattr(s, "agent", None) or {}).get("id") == agent_id):
                out.append(str(cid))
        return out
    except Exception:  # noqa: BLE001
        return []


def _resolve_call(booking: dict) -> Optional[str]:
    cid = current_call_id() or booking.get("conversation_id")
    if cid:
        return str(cid)
    live = _live_call_ids(booking.get("agent_id"))
    return live[0] if len(live) == 1 else None


# ------------------------------------------------------------------------------------------------ the words

DAYS = {"en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "hi": ["सोम", "मंगल", "बुध", "गुरु", "शुक्र", "शनि", "रवि"],
        "mr": ["सोम", "मंगळ", "बुध", "गुरु", "शुक्र", "शनि", "रवि"]}
MONTHS = {"en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
          "hi": ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
          "mr": ["जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून", "जुलै", "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर",
                 "नोव्हेंबर", "डिसेंबर"]}
RELATIONS = {
    "hi": {"mother": "आपकी माँ", "father": "आपके पिता", "son": "आपके बेटे", "daughter": "आपकी बेटी", "wife": "आपकी पत्नी",
           "husband": "आपके पति", "brother": "आपके भाई", "sister": "आपकी बहन"},
    "mr": {"mother": "तुमची आई", "father": "तुमचे वडील", "son": "तुमचा मुलगा", "daughter": "तुमची मुलगी",
           "wife": "तुमची पत्नी", "husband": "तुमचे पती", "brother": "तुमचा भाऊ", "sister": "तुमची बहीण"}}
SELF_WORDS = ("", "self", "me", "myself", "own")

# Placeholders {1}.. are Meta's positional body parameters {{1}}.. . Each text starts and ends with plain words and carries
# enough words per parameter, because Meta's template review refuses a body that starts or ends with a parameter. Parameters:
#   booked, cancelled: 1 business, 2 person, 3 when, 4 booking id      moved: 1 business, 2 person, 3 old when, 4 new when, 5 id
TEXTS: Dict[str, Dict[str, str]] = {
    "booked": {"en": "Hello! Your booking at {1} for {2} is confirmed: {3}. Booking ID: {4}. To change or cancel it, just call us again.",
               "hi": "नमस्ते! {1} में {2} की बुकिंग पक्की हो गई है: {3}। बुकिंग आईडी: {4}। बदलने या रद्द करने के लिए हमें दोबारा कॉल करें।",
               "mr": "नमस्कार! {1} येथे {2} यांचे बुकिंग निश्चित झाले आहे: {3}. बुकिंग आयडी: {4}. बदल किंवा रद्द करण्यासाठी आम्हाला पुन्हा कॉल करा."},
    "moved": {"en": "Hello! Your booking at {1} for {2} has been moved from {3} to {4}. Booking ID: {5}. To change it again, just call us.",
              "hi": "नमस्ते! {1} में {2} की बुकिंग {3} से बदलकर {4} कर दी गई है। बुकिंग आईडी: {5}। फिर से बदलने के लिए हमें कॉल करें।",
              "mr": "नमस्कार! {1} येथे {2} यांचे बुकिंग आता {3} ऐवजी {4} आहे. बुकिंग आयडी: {5}. पुन्हा बदलण्यासाठी आम्हाला कॉल करा."},
    "cancelled": {"en": "Hello! Your booking at {1} for {2} on {3} has been cancelled. Booking ID: {4}. To book again, just call us.",
                  "hi": "नमस्ते! {1} में {2} की {3} वाली बुकिंग रद्द कर दी गई है। बुकिंग आईडी: {4}। दोबारा बुक करने के लिए हमें कॉल करें।",
                  "mr": "नमस्कार! {1} येथे {2} यांचे {3} असलेले बुकिंग रद्द झाले आहे. बुकिंग आयडी: {4}. पुन्हा बुक करण्यासाठी आम्हाला कॉल करा."}}


def fill(text: str, params: List[str]) -> str:
    return re.sub(r"\{(\d+)\}", lambda m: params[int(m.group(1)) - 1], text)


def _clock(hh: int, mm: int, lang: str) -> str:
    h12 = hh % 12 or 12
    t = f"{h12}:{mm:02d}"
    if lang == "hi":
        part = "सुबह" if hh < 12 else "दोपहर" if hh < 16 else "शाम" if hh < 20 else "रात"
        return f"{part} {t} बजे"
    if lang == "mr":
        part = "सकाळी" if hh < 12 else "दुपारी" if hh < 16 else "संध्याकाळी" if hh < 20 else "रात्री"
        return f"{part} {t} वाजता"
    return f"{t} {'am' if hh < 12 else 'pm'}"


def _when_of(date_s: Any, time_s: Any, start: Any = None) -> Optional[datetime]:
    for raw in (start,):
        if raw:
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                pass
    try:
        d = datetime.strptime(str(date_s), "%Y-%m-%d")
    except ValueError:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})", str(time_s or ""))
    return d.replace(hour=int(m.group(1)), minute=int(m.group(2))) if m else d


def when_words(date_s: Any, time_s: Any, lang: str, start: Any = None, nights: Any = None) -> str:
    """"Thu 6 Nov, 3:00 pm" (a stay: "Thu 6 Nov, 2 nights"), in the language."""
    dt = _when_of(date_s, time_s, start)
    if dt is None:
        return str(date_s or "")
    day = f"{DAYS[lang][dt.weekday()]} {dt.day} {MONTHS[lang][dt.month - 1]}"
    try:
        n = int(nights or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        unit = {"en": "night" if n == 1 else "nights", "hi": "रात", "mr": "रात्र" if n == 1 else "रात्री"}[lang]
        return f"{day}, {n} {unit}"
    if not re.match(r"^\d{1,2}:\d{2}", str(time_s or "")) and not start:
        return day
    return f"{day}, {_clock(dt.hour, dt.minute, lang)}"


def person_words(name: str, relation: Optional[str], lang: str) -> str:
    name = (name or "").strip() or ("you" if lang == "en" else "आप" if lang == "hi" else "तुम्ही")
    rel = (relation or "").strip().lower()
    if rel in SELF_WORDS:
        return name
    if lang == "en":
        return f"your {rel}, {name}"
    phrase = RELATIONS[lang].get(rel)
    return f"{phrase}, {name}" if phrase else name


def compose(item: dict, lang: str, business: str) -> Tuple[str, str, List[str]]:
    """(template name, text as the caller reads it, body parameters) for an outbox item."""
    snap = item["snap"]
    kind = item["kind"]
    person = person_words(snap.get("name", ""), snap.get("relation"), lang)
    nights = snap.get("nights")
    new = when_words(snap.get("date"), snap.get("time"), lang, snap.get("start"), nights)
    bid = str(item["booking_id"])
    if kind == "moved":
        old = item.get("old") or {}
        params = [business, person, when_words(old.get("date"), old.get("time"), lang, old.get("start"), nights), new, bid]
    else:
        params = [business, person, new, bid]
    return whatsapp.template_name(kind), fill(TEXTS[kind][lang], params), params


# ------------------------------------------------------------------------------------------------ lookups

def _agent(agent_id: Optional[str]) -> Optional[dict]:
    if not agent_id:
        return None
    try:
        try:
            from core import state  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            from backend.core import state  # noqa: PLC0415
        return state.local_db.get("agents", agent_id)
    except Exception:  # noqa: BLE001
        return None


def _norm_lang(value: Any) -> Optional[str]:
    code = str(value or "").split("-")[0].strip().lower()
    return code if code in LANGUAGES else None


def _language(item: dict, owner: Optional[str]) -> str:
    cid = item.get("conversation_id")
    if cid:
        try:
            try:
                from core import state  # noqa: PLC0415
            except ImportError:  # pragma: no cover
                from backend.core import state  # noqa: PLC0415
            conv = state.local_db.get("conversations", cid)
            lang = _norm_lang((conv or {}).get("reply_language"))
            if lang:
                return lang
        except Exception:  # noqa: BLE001
            pass
    if owner:
        try:
            try:
                from services import caller_profiles  # noqa: PLC0415
            except ImportError:  # pragma: no cover
                from backend.services import caller_profiles  # noqa: PLC0415
            prof = caller_profiles.lookup_any_age(owner, "+91" + item["phone"])
            lang = _norm_lang((prof or {}).get("preferred_language"))
            if lang:
                return lang
        except Exception:  # noqa: BLE001
            pass
    return "en"


# ------------------------------------------------------------------------------------------------ the log

def _log(data: dict, item: dict, state: str, *, text: str = "", template: str = "", language: str = "en",
         message_id: Optional[str] = None, error: Optional[str] = None, reason: Optional[str] = None) -> dict:
    row = {"id": uuid.uuid4().hex[:12], "at": _iso(), "state": state, "kind": item["kind"], "booking_id": item["booking_id"],
           "phone_masked": mask(item["phone"]), "text": text, "template": template, "language": language,
           "message_id": message_id, "error": error, "reason": reason, "owner": item.get("owner"),
           "source": item.get("source")}
    data["messages"].append(row)
    log.info("whatsapp %s: booking %s %s %s%s", state, item["booking_id"], item["kind"], mask(item["phone"]),
             f" ({error or reason})" if (error or reason) else "")
    return row


def _was_confirmed(data: dict, booking_id: str) -> bool:
    return any(m.get("booking_id") == booking_id and m.get("state") in ("sent", "preview") for m in data["messages"])


# ------------------------------------------------------------------------------------------------ collapse

def _slot(snap: dict) -> Tuple[Any, Any]:
    return (snap.get("date"), snap.get("time"))


def _snap(b: dict) -> dict:
    return {k: b.get(k) for k in ("name", "date", "time", "start", "end", "schedule_name", "relation", "booked_by", "nights")}


def _merge(data: dict, old: Optional[dict], kind: str, booking: dict, previous: Optional[dict]) -> Optional[dict]:
    """The outbox item after one more change, or None when the changes cancel out."""
    snap = _snap(booking)
    version = booking.get("version")
    moved_from = booking.get("moved_from") or (_snap(previous) if previous else None)
    if old is None:
        item = {"first_kind": kind, "kind": kind, "snap": snap, "old": moved_from if kind == "moved" else None}
    else:
        item = dict(old)
        if kind == "booked":
            item.update(snap=snap)
        elif kind == "moved":
            if old["kind"] == "moved":
                item["old"] = old.get("old") or moved_from
            elif old["kind"] == "booked":
                pass                                  # still "booked", at the new time
            else:
                item["kind"], item["old"] = "moved", moved_from
            item["snap"] = snap
            if item["kind"] == "moved" and item.get("old") and _slot(item["old"]) == _slot(snap):
                return None                           # moved away and back: nothing changed
        else:                                         # cancelled
            if old["first_kind"] == "booked":
                if not _was_confirmed(data, str(booking.get("booking_id"))):
                    return None                       # booked and cancelled on one call: the caller never got a confirmation
                item.update(kind="cancelled", snap=snap)
            else:
                held = old.get("old") if old["kind"] == "moved" and old.get("old") else None
                item.update(kind="cancelled", snap={**snap, **{k: held.get(k) for k in ("date", "time", "start")}}
                            if held else snap)
    item["version"] = version
    return item


# ------------------------------------------------------------------------------------------------ timers

def _arm(item_id: str, send_at: float) -> None:
    with _lock:
        t = _timers.pop(item_id, None)
        if t:
            t.cancel()
        t = threading.Timer(max(0.0, send_at - _now()), _fire, [item_id])
        t.daemon = True
        _timers[item_id] = t
        t.start()


def _fire(item_id: str) -> None:
    try:
        with _lock:
            _timers.pop(item_id, None)
            item = _load()["items"].get(item_id)
            if not item or item.get("send_at", 0) > _now() + 0.05:
                return
        send_item(item_id)
    except Exception:  # noqa: BLE001
        log.exception("whatsapp flush failed")


def reset() -> None:
    """Cancel every timer and forget the in-memory call ends (tests; the file is left alone)."""
    with _lock:
        for t in _timers.values():
            t.cancel()
        _timers.clear()
        _ended.clear()


def resume() -> int:
    """Start-up: re-arm every pending item a restart left behind. Returns how many."""
    with _lock:
        items = _load()["items"]
        for iid, it in items.items():
            _arm(iid, float(it.get("send_at") or _now()))
        return len(items)


# ------------------------------------------------------------------------------------------------ the interface

def _item_id(scope: str, booking_id: str) -> str:
    return f"{scope}|{booking_id}"


def booking_changed(kind: str, booking: dict, previous: Optional[dict] = None, *, source: str = "agent") -> dict:
    """A booking was booked, moved or cancelled. Records the change; the message goes after the call. Never raises.
    Returns {"status": "queued"} | {"status": "ask", "number_ending": "3210"} | {"status": "off"}."""
    try:
        return _booking_changed(kind, booking or {}, previous, source)
    except Exception:  # noqa: BLE001
        log.exception("booking_changed failed")
        return {"status": "off"}


def _booking_changed(kind: str, booking: dict, previous: Optional[dict], source: str) -> dict:
    off = {"status": "off"}
    phone = _digits(booking.get("phone"))
    bid = str(booking.get("booking_id") or booking.get("id") or "")
    if kind not in KINDS or not phone or not bid or not enabled():
        return off
    booking = {**booking, "booking_id": bid}
    staff = source == "staff"
    with _lock:
        data = _load()
        consent = data["consent"].get(phone)
        if consent and not consent.get("agreed"):
            return off
        cid = None if staff else _resolve_call(booking)
        scope = "staff" if staff else (cid or f"booking:{bid}")
        base = {"booking_id": bid, "phone": phone, "agent_id": booking.get("agent_id"), "conversation_id": cid,
                "source": "staff" if staff else "agent", "scope": scope}
        if staff and not consent:
            _log(data, {**base, "kind": kind}, "skipped", reason="no consent for WhatsApp on this number")
            _save(data)
            return off
        iid = _item_id(scope, bid)
        item = _merge(data, data["items"].get(iid), kind, booking, previous)
        if item is None:
            data["items"].pop(iid, None)
            t = _timers.pop(iid, None)
            if t:
                t.cancel()
            _save(data)
            return {"status": "queued"} if consent else off
        item.update(base)
        agent = _agent(item.get("agent_id"))
        item["owner"] = (agent or {}).get("user_id")
        item["business"] = (agent or {}).get("name") or booking.get("schedule_name") or "Vakya"
        item["changed_at"] = _now()
        ended = cid is not None and cid in _ended
        item["send_at"] = _now() + (send_delay_s() if (staff or ended) else backstop_s())
        data["items"][iid] = item
        ask = False
        if not consent and not staff:
            ask = scope not in data["asks"]
            data["asks"][scope] = {"phone": phone, "at": _now()}
        _save(data)
        _arm(iid, item["send_at"])
    return {"status": "ask", "number_ending": phone[-4:]} if ask else {"status": "queued"}


def record_consent(agreed: bool) -> dict:
    """The caller's answer to "Shall I send the confirmation on WhatsApp?" on the current call. Never raises."""
    try:
        return _record_consent(bool(agreed))
    except Exception:  # noqa: BLE001
        log.exception("record_consent failed")
        return {"status": "off"}


def _record_consent(agreed: bool) -> dict:
    with _lock:
        data = _load()
        asks = data["asks"]
        cid = current_call_id()
        scope = cid if cid in asks else None
        if scope is None:
            live = [c for c in _live_call_ids() if c in asks]
            scope = live[0] if len(live) == 1 else None
        if scope is None:
            fresh = [(a.get("at", 0), s) for s, a in asks.items() if _now() - a.get("at", 0) < ASK_FRESH_S]
            scope = max(fresh)[1] if fresh else None
        if scope is None:
            return {"status": "no_question_pending"}
        phone = asks.pop(scope)["phone"]
        data["consent"][phone] = {"agreed": agreed, "at": _iso(), "conversation_id": None if scope.startswith("booking:") else scope}
        if not agreed:
            for iid, it in list(data["items"].items()):
                if it["phone"] == phone:
                    data["items"].pop(iid)
                    t = _timers.pop(iid, None)
                    if t:
                        t.cancel()
                    _log(data, it, "skipped", reason="the caller said no to WhatsApp")
        _save(data)
        return {"status": "recorded", "agreed": agreed, "number_ending": phone[-4:]}


def consent_state(phone: Any) -> Optional[bool]:
    """The number's WhatsApp consent: True (agreed), False (declined), None (never answered). Never raises."""
    try:
        number = _digits(phone)
        if not number:
            return None
        row = _load()["consent"].get(number)
        return bool(row.get("agreed")) if row else None
    except Exception:  # noqa: BLE001
        return None


def asks_consent(phone: Any) -> bool:
    """Should the read-back of a booking for `phone` also say that the confirmation goes out on WhatsApp? Yes while the number
    has not answered and WhatsApp confirmations are on (the caller's yes to the read-back is then the consent)."""
    return bool(_digits(phone)) and enabled() and consent_state(phone) is None


def consent_from_readback(phone: Any) -> dict:
    """The caller said yes to a read-back that named the WhatsApp confirmation: that yes is the consent, so the agent never
    asks a separate question. Recorded as `record_consent` records an answer (agreed, time, conversation id); a number that
    already answered (either way) is left as it is. Never raises."""
    try:
        number = _digits(phone)
        if not number or not enabled():
            return {"status": "off"}
        with _lock:
            data = _load()
            if number in data["consent"]:
                return {"status": "already_answered", "agreed": bool(data["consent"][number].get("agreed"))}
            data["consent"][number] = {"agreed": True, "at": _iso(), "conversation_id": current_call_id(), "via": "read-back"}
            for scope, ask in list(data["asks"].items()):
                if ask.get("phone") == number:
                    data["asks"].pop(scope)
            _save(data)
        return {"status": "recorded", "agreed": True, "number_ending": number[-4:]}
    except Exception:  # noqa: BLE001
        log.exception("consent_from_readback failed")
        return {"status": "off"}


def call_ended(conversation_id: Optional[str]) -> int:
    """The call ended (a hangup callback or the stream closing; safe to call twice): its messages go after the delay.
    Never raises. Returns how many items were pulled forward."""
    if not conversation_id:
        return 0
    cid = str(conversation_id)
    try:
        with _lock:
            _ended[cid] = _now()
            while len(_ended) > 500:
                _ended.pop(next(iter(_ended)))
            data = _load()
            n = 0
            for iid, it in data["items"].items():
                if it.get("scope") == cid:
                    at = min(float(it.get("send_at") or 1e18), _now() + send_delay_s())
                    it["send_at"] = at
                    _arm(iid, at)
                    n += 1
            if n:
                _save(data)
            return n
    except Exception:  # noqa: BLE001
        log.exception("call_ended failed")
        return 0


def send_item(item_id: str) -> Optional[dict]:
    """Send (or preview, or skip) one outbox item now; returns its log row. Safe to call twice: the item is claimed first."""
    with _lock:
        data = _load()
        item = data["items"].pop(item_id, None)
        if item is None:
            return None
        key = f"{item['booking_id']}:{item.get('version')}:{item['kind']}"
        if key in data["sent_keys"]:
            _save(data)
            return None
        consent = data["consent"].get(item["phone"])
        if not (consent and consent.get("agreed")):
            row = _log(data, item, "skipped", reason="no consent for WhatsApp on this number")
            _save(data)
            return row
        agent = _agent(item.get("agent_id"))
        owner = item.get("owner") or (agent or {}).get("user_id")
        lang = _language(item, owner)
        template, text, params = compose(item, lang, item.get("business") or "Vakya")
        if whatsapp.configured():
            data["sent_keys"][key] = "sending"
            _save(data)
        elif preview_on():
            data["sent_keys"][key] = "preview"
            row = _log(data, item, "preview", text=text, template=template, language=lang)
            _save(data)
            return row
        else:
            row = _log(data, item, "skipped", text=text, template=template, language=lang, reason="WhatsApp is not set up")
            _save(data)
            return row
    try:
        wamid = whatsapp.send_template(item["phone"], template, lang, params)
        state, kw = "sent", {"message_id": wamid}
    except whatsapp.WhatsAppError as e:
        state, kw = "failed", {"error": str(e) + (f" (code {e.code})" if e.code else "")}
    except Exception as e:  # noqa: BLE001
        state, kw = "failed", {"error": f"unexpected error ({e.__class__.__name__})"}
    with _lock:
        data = _load()
        data["sent_keys"][key] = kw.get("message_id") or "failed"
        row = _log(data, item, state, text=text, template=template, language=lang, **kw)
        _save(data)
        return row


def flush_due(now: Optional[float] = None) -> int:
    """Send every item whose time has come (what the timers do; also handy for a restart or a test)."""
    n = 0
    t = now if now is not None else _now()
    for iid, it in list(_load()["items"].items()):
        if float(it.get("send_at") or 0) <= t and send_item(iid):
            n += 1
    return n


# ------------------------------------------------------------------------------------------------ for the desk

def recent_messages(owner: Optional[str] = None, limit: int = 50) -> List[dict]:
    with _lock:
        rows = [m for m in _load()["messages"] if owner is None or m.get("owner") in (None, owner)]
    out = []
    for m in reversed(rows[-limit:] if limit else rows):
        out.append({k: m.get(k) for k in ("id", "at", "state", "kind", "booking_id", "phone_masked", "text", "language",
                                          "template", "message_id", "error", "reason", "source")})
    return out


def counts(owner: Optional[str] = None) -> dict:
    with _lock:
        data = _load()
    rows = [m for m in data["messages"] if owner is None or m.get("owner") in (None, owner)]
    c = {s: sum(1 for m in rows if m.get("state") == s) for s in ("sent", "preview", "failed", "skipped")}
    c["pending"] = len(data["items"])
    c["consented_numbers"] = sum(1 for v in data["consent"].values() if v.get("agreed"))
    return c
