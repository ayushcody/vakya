"""Vobiz, Vakya's only phone carrier: the Call API, VobizXML answer documents and the bidirectional Audio Stream.

Sources (read 4 Nov 2026):
  https://www.vobiz.ai/docs/call/make-call.md                POST https://api.vobiz.ai/api/v1/Account/{auth_id}/Call/, headers X-Auth-ID and
                                                             X-Auth-Token; body from, to, answer_url, answer_method, hangup_url, hangup_method;
                                                             200 {"message": "Call fired", "request_uuid": ...} where request_uuid is "equivalent
                                                             to call_uuid"; 401 bad headers or lowercase path, 402 balance too low, 429 CPS or
                                                             concurrent-call limit, "From number not owned by account"
  https://www.vobiz.ai/docs/call/hangup-call.md              DELETE .../Call/{call_uuid}/ (204)
  https://www.vobiz.ai/docs/call/retrieve-live-call.md       GET .../Call/{call_uuid}/?status=live -> call_status, session_start, call_uuid, ...
  https://www.vobiz.ai/docs/cdr.md                           the finished call: duration, billsec, answer_time, start_time, end_time, hangup_cause_name
  https://www.vobiz.ai/docs/xml/stream.md                    <Stream bidirectional keepCallAlive contentType statusCallbackUrl>wss://...</Stream>;
                                                             contentType audio/x-mulaw;rate=8000 or audio/x-l16;rate=8000|16000
  https://www.vobiz.ai/docs/xml/stream/stream-events.md      start {callId, streamId, mediaFormat}, media {payload}, playedStream {name}, clearedAudio;
                                                             playAudio {streamId, media{contentType, sampleRate, payload}}, checkpoint, clearAudio.
                                                             There is NO inbound `stop`: Vobiz closes the WebSocket, and `start` carries no from/to
  https://www.vobiz.ai/docs/xml/hangup.md                    <Hangup reason="rejected"/> as the first element of an unanswered inbound call ends it "to
                                                             avoid answering (and billing) it"; reasons: rejected | busy
  https://www.vobiz.ai/docs/xml/request.md                   what Vobiz POSTs (form-urlencoded) to answer_url and hangup_url: CallUUID, From, To,
                                                             Direction, CallStatus, HangupCause, Duration, BillDuration
  https://www.vobiz.ai/docs/applications.md                  an XML Application's answer_url answers a call to the number attached to it; it must
                                                             respond within 10 seconds and failed webhooks are retried up to 3 times

What is and is not confirmed:
  * Confirmed by those pages: the call request and its answer, the answer XML, `start`, `playAudio`, `clearAudio`, `checkpoint`,
    hangup, the form fields of the answer and hangup requests, the Hangup element.
  * NOT documented by Vobiz and assumed (Plivo's shapes): the `media` frame beyond `media.payload`, `dtmf`, and the field that
    ties the answer request of an outbound call to its Call API request. The status callback is read as Plivo-style (CallUUID,
    CallStatus, Duration, HangupCause). Everything marked "assumed" is unverified until a real call has run through it.
  * Refusal wording ("insufficient", "not owned by account") is partly from the make-call page and partly a guess, cheap to
    extend; a refusal that is not recognised stops the call with Vobiz's own message."""
from __future__ import annotations

import asyncio
import random
from typing import Any, Dict, Optional
from xml.sax.saxutils import escape

from .. import phone_audio as pa
from .base import (AUTH_FAILED, EXHAUSTED, NOT_ALLOWED, RATE_LIMITED, Event, Provider, ProviderHTTPError, StreamCodec,
                   duration_of, field, http_request, json_message, normalise_status)

VOBIZ_BASE = "https://api.vobiz.ai/api/v1"
EXHAUSTED_WORDS = ("insufficient", "balance", "credit", "recharge", "payment required", "low balance")
NOT_ALLOWED_WORDS = ("not owned by account", "sandbox", "not verified", "unverified", "whitelist", "not authorised",
                     "not authorized to call", "geo permission", "destination not")
STATUS_WORDS = {"cancel": "canceled", "timeout": "no-answer", "rejected": "failed", "busyline": "busy", "hangup": "completed"}
RETRY_429_AFTER_S = 1.0       # Vobiz: "429 CPS or concurrent-call limit exceeded. Back off with jitter." One retry, then the refusal.


class VobizCodec(StreamCodec):
    """Vobiz Audio Streams. We ask for audio/x-mulaw at 8000 Hz; `start.mediaFormat` says what really arrives, and audio goes
    back in the same format (`playAudio.media.contentType` and `sampleRate` must match the stream, and `playAudio` carries the
    streamId at the top level: stream-events.md)."""
    name = "vobiz"

    def __init__(self) -> None:
        self.encoding = "mulaw"              # "mulaw" | "l16"
        self.rate = 8000

    def parse(self, text: str) -> Optional[Event]:
        ev = self.load(text)
        if ev is None:
            return None
        kind = ev.get("event")
        if kind == "start":
            start = ev.get("start") or {}
            fmt = start.get("mediaFormat") or start.get("media_format") or {}
            enc = str(fmt.get("encoding") or "audio/x-mulaw").lower()
            self.encoding = "l16" if "l16" in enc else "mulaw"
            try:
                self.rate = int(fmt.get("sampleRate") or fmt.get("sample_rate") or 8000)
            except (TypeError, ValueError):
                self.rate = 8000
            if self.encoding == "mulaw":
                self.rate = 8000
            # Vobiz's `start` carries no from/to (stream-events.md): `from` is read in case it is added, else "" and the
            # caller's number comes from the answer request (routers/phone.py, _inbound_callers).
            return Event("start", stream_id=start.get("streamId") or start.get("stream_id") or ev.get("streamId"),
                         call_id=start.get("callId") or start.get("call_id") or start.get("callUuid"),
                         caller=str(start.get("from", "")), rate=self.rate)
        if kind == "media":
            raw = self.b64decode((ev.get("media") or {}).get("payload"))
            if raw is None:
                return None
            return Event("media", pcm=pa.ulaw_decode(raw) if self.encoding == "mulaw" else raw[: len(raw) - len(raw) % 2])
        if kind == "dtmf":                       # assumed shape (Vobiz does not document it): {"event": "dtmf", "dtmf": {"digit": "5"}}
            return Event("dtmf", digit=self.digit_of(ev.get("dtmf")))
        if kind == "playedStream":
            return Event("mark", name=str(ev.get("name") or ""))
        if kind == "stop":                       # never sent inbound by Vobiz; harmless if it is
            return Event("stop")
        return None                              # "clearedAudio" and anything new

    def media(self, pcm: bytes, stream_id: Optional[str]) -> dict:
        audio = pa.ulaw_encode(pcm) if self.encoding == "mulaw" else pcm
        return {"event": "playAudio", "streamId": stream_id,
                "media": {"contentType": "audio/x-mulaw" if self.encoding == "mulaw" else "audio/x-l16",
                          "sampleRate": self.rate, "payload": self.b64(audio)}}

    def clear(self, stream_id: Optional[str]) -> dict:
        return {"event": "clearAudio", "streamId": stream_id}

    def mark(self, name: str, stream_id: Optional[str]) -> dict:
        return {"event": "checkpoint", "streamId": stream_id, "name": name}


def stream_xml(stream_url: str, transfer_to: Optional[str] = None, caller_id: Optional[str] = None) -> str:
    """The answer document that connects a call to our WebSocket. keepCallAlive="true" runs the stream exclusively and the
    elements after it only once it disconnects (stream.md). With none after it the call ends with the stream (and the agent
    also hangs it up through the API). With `transfer_to` (PHONE_TRANSFER_MODE=flow) a <Dial> follows, so a stream the agent
    closes for a transfer carries the call on to that number (https://www.vobiz.ai/docs/xml/dial/number.md: callerId must be
    a Vobiz number). Whether the Dial runs after a stream closed by us is ASSUMED from stream.md, not tried."""
    dial = ""
    if transfer_to:
        cid = f' callerId="{escape(caller_id, {chr(34): "&quot;"})}"' if caller_id else ""
        dial = f"<Dial{cid} timeout=\"30\"><Number>{escape(transfer_to)}</Number></Dial>"
    return ('<?xml version="1.0" encoding="UTF-8"?><Response><Stream bidirectional="true" keepCallAlive="true" '
            'contentType="audio/x-mulaw;rate=8000">' + escape(stream_url) + "</Stream>" + dial + "</Response>")


def reject_xml() -> str:
    """The answer document of a missed-call number: <Hangup reason="rejected"/> as the first element, so the unanswered call
    is ended without being answered or billed (https://www.vobiz.ai/docs/xml/hangup.md)."""
    return '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup reason="rejected"/></Response>'


class Vobiz(Provider):
    name = "vobiz"
    display = "Vobiz"
    verified = False
    verification_note = ("Written from Vobiz's docs (call API, headers, <Stream> XML, start / playAudio / clearAudio / checkpoint, "
                         "hangup, the Hangup element and the answer / hangup requests are documented); the dtmf shape and how "
                         "the answer request of an outbound call names its Call API request are assumed. Not yet run against a "
                         "real account.")
    VARS = (("auth_id", "VOBIZ_AUTH_ID"), ("token", "VOBIZ_AUTH_TOKEN"), ("caller_id", "VOBIZ_CALLER_ID"))
    OPTIONAL = (("base_url", "VOBIZ_BASE_URL", VOBIZ_BASE),)
    api_hangup = True        # keepCallAlive keeps the call up after the stream: the agent ends it through DELETE .../Call/{uuid}/
    inbound_how = ("In the Vobiz console create an XML Application whose Answer URL is the address (method POST), then attach "
                   "your number to that application.")

    def codec(self) -> StreamCodec:
        return VobizCodec()

    def answer_document(self, stream_url: str, transfer_to: Optional[str] = None) -> str:
        account = self.account()
        return stream_xml(stream_url, transfer_to, account["caller_id"] if account else None)

    # ---- HTTP -----------------------------------------------------------------------------------------------
    def base_url(self, account: Dict[str, str]) -> str:
        return (account.get("base_url") or VOBIZ_BASE).rstrip("/")

    def auth_kwargs(self, account: Dict[str, str]) -> Dict[str, Any]:
        return {"headers": {"X-Auth-ID": account["auth_id"], "X-Auth-Token": account["token"],
                            "Content-Type": "application/json"}}

    def call_url(self, account: Dict[str, str], call_id: str = "") -> str:
        # "The path is case-sensitive and needs the trailing slash" (quick-start.md): /Account/{auth_id}/Call/
        return f"{self.base_url(account)}/Account/{account['auth_id']}/Call/" + (f"{call_id}/" if call_id else "")

    async def place_call(self, account, to, stream_url, status_url, answer_url=None):
        body: Dict[str, Any] = {"from": account["caller_id"], "to": to, "answer_url": answer_url, "answer_method": "POST"}
        if status_url:
            body["hangup_url"] = status_url
            body["hangup_method"] = "POST"
        for attempt in (1, 2):
            r = await http_request("post", self.call_url(account), 20.0, json=body, **self.auth_kwargs(account))
            if r.status_code == 429 and attempt == 1:     # a refusal: the call was not placed, so asking once more is safe
                await asyncio.sleep(RETRY_429_AFTER_S + random.random())
                continue
            break
        if r.status_code >= 300:
            raise ProviderHTTPError(r.status_code, r.text or "")
        try:
            return (r.json() or {}).get("request_uuid")
        except Exception:  # noqa: BLE001
            return None

    async def call_status(self, account, call_id):
        """A call that is live answers `?status=live`; one that is over is in the call detail record. Neither: still queued."""
        live = await http_request("get", self.call_url(account, call_id) + "?status=live", 10.0, **self.auth_kwargs(account))
        if live.status_code < 300:
            try:
                f = live.json() or {}
            except Exception:  # noqa: BLE001
                f = {}
            return {"status": normalise_status(f.get("call_status") or "in-progress"), "duration_s": None,
                    "started_at": f.get("session_start") or None, "ended_at": None, "message": None}
        if live.status_code != 404:
            raise ProviderHTTPError(live.status_code, live.text or "")
        cdr = await http_request("get", self.call_url(account, call_id), 10.0, **self.auth_kwargs(account))
        if cdr.status_code == 404:
            return {"status": "queued", "duration_s": None, "started_at": None, "ended_at": None, "message": None}
        if cdr.status_code >= 300:
            raise ProviderHTTPError(cdr.status_code, cdr.text or "")
        try:
            f = cdr.json() or {}
        except Exception:  # noqa: BLE001
            f = {}
        if isinstance(f.get("data"), dict):      # the list endpoint wraps its rows in {success, data}; the single one may too
            f = f["data"]
        cause = str(f.get("hangup_cause_name") or "")
        low = cause.lower()
        duration = duration_of(f.get("duration") if f.get("duration") is not None else f.get("call_duration"))
        billsec = duration_of(f.get("billsec"))
        if "busy" in low:
            status = "busy"
        elif "no answer" in low or "no_answer" in low or "timeout" in low:
            status = "no-answer"
        elif "cancel" in low:
            status = "canceled"
        elif f.get("answer_time") or (billsec or 0) > 0:
            status = "completed"
        else:
            status = "failed"
        return {"status": status, "duration_s": billsec if billsec is not None else duration,
                "started_at": f.get("answer_time") or f.get("start_time") or f.get("initiation_time") or None,
                "ended_at": f.get("end_time") or None, "message": cause or None}

    async def hangup(self, account, call_id) -> bool:
        try:
            r = await http_request("delete", self.call_url(account, call_id), 10.0, **self.auth_kwargs(account))
            return r.status_code < 300
        except Exception:  # noqa: BLE001 - best effort
            return False

    # ---- refusals and callbacks -------------------------------------------------------------------------------
    def reason(self, body: str) -> str:
        return json_message(body, "error", "message", "detail")[:300]

    def classify_refusal(self, status: int, body: str) -> Optional[str]:
        low = self.reason(body).lower()
        if status == 402 or any(w in low for w in EXHAUSTED_WORDS):
            return EXHAUSTED
        if any(w in low for w in NOT_ALLOWED_WORDS):
            return NOT_ALLOWED
        if status == 429:
            return RATE_LIMITED
        if status in (401, 403):
            return AUTH_FAILED
        return None

    def parse_status_callback(self, form):
        """The hangup_url request (https://www.vobiz.ai/docs/xml/request.md): CallUUID, CallStatus, Duration, BillDuration,
        HangupCause. Read leniently: Plivo-style spellings are accepted too."""
        sid = field(form, "CallUUID", "call_uuid", "RequestUUID")
        if not sid:
            return None
        raw = field(form, "CallStatus", "Status")
        status = normalise_status(raw)
        if status == "unknown":
            word = str(raw or field(form, "Event") or "").lower().replace(" ", "").replace("-", "").replace("_", "")
            status = STATUS_WORDS.get(word, "unknown")
            if status == "unknown" and "hangup" in str(field(form, "Event") or "").lower():
                status = "completed"
        return {"sid": str(sid), "status": status, "duration_s": duration_of(field(form, "BillDuration", "Duration")),
                "ended_at": field(form, "EndTime"), "message": field(form, "HangupCauseName", "HangupCause")}


PROVIDER = Vobiz()
