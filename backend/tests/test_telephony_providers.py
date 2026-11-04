"""The Vobiz HTTP side (services/telephony/vobiz.py), against a fake HTTP client: the request that places a call (URL,
auth headers, body, answer and hangup addresses), the call id read from the answer, the call status, the hangup, what a
refusal means, the answer documents and the registry. Sources: https://www.vobiz.ai/docs/call/make-call.md, hangup-call.md,
retrieve-live-call.md, cdr.md, xml/stream.md, xml/hangup.md. No network."""
import asyncio
import json

import httpx
import pytest

from services.telephony import registry
from services.telephony.base import ProviderHTTPError, ProviderUnreachable
from services.telephony.vobiz import PROVIDER as VOBIZ, reject_xml, stream_xml

WSS = "wss://abc.ngrok.app/api/phone/stream/TOKEN"
STATUS = "https://abc.ngrok.app/api/phone/status-callback/TOKEN"
ANSWER = "https://abc.ngrok.app/api/phone/answer/TOKEN"
TO = "+919876543210"
VB = {"auth_id": "VBID", "token": "vtok", "caller_id": "+912240000000", "base_url": "https://api.vobiz.ai/api/v1"}


class Http:
    """Records every request; answers from `queue` (httpx.Response objects), else 200 with `{}`."""
    sent: list = []
    queue: list = []
    boom = False

    def __init__(self, *a, **k):
        self.timeout = k.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _do(self, method, url, **kw):
        Http.sent.append({"method": method, "url": url, **kw})
        if Http.boom:
            raise httpx.ConnectError("down")
        return Http.queue.pop(0) if Http.queue else httpx.Response(200, json={})

    async def post(self, url, **kw):
        return await self._do("post", url, **kw)

    async def get(self, url, **kw):
        return await self._do("get", url, **kw)

    async def delete(self, url, **kw):
        return await self._do("delete", url, **kw)


@pytest.fixture()
def http(monkeypatch):
    Http.sent, Http.queue, Http.boom = [], [], False
    monkeypatch.setattr(httpx, "AsyncClient", Http)
    from services.telephony import vobiz
    monkeypatch.setattr(vobiz, "RETRY_429_AFTER_S", 0.0)
    return Http


def run(coro):
    return asyncio.run(coro)


def reply(status=200, body=None, text=None):
    return httpx.Response(status, text=text) if text is not None else httpx.Response(status, json=body if body is not None else {})


def test_vobiz_places_the_call_with_its_own_base_url_auth_headers_and_answer_url(http):
    http.queue = [reply(200, {"api_id": "a", "message": "Call fired", "request_uuid": "vb-1"})]
    cid = run(VOBIZ.place_call(VB, TO, WSS, STATUS, ANSWER))
    assert cid == "vb-1"
    r = http.sent[0]
    assert r["method"] == "post" and r["url"] == "https://api.vobiz.ai/api/v1/Account/VBID/Call/" and "auth" not in r
    assert r["headers"]["X-Auth-ID"] == "VBID" and r["headers"]["X-Auth-Token"] == "vtok"
    assert r["json"] == {"from": "+912240000000", "to": TO, "answer_url": ANSWER, "answer_method": "POST",
                         "hangup_url": STATUS, "hangup_method": "POST"}


def test_the_base_url_can_be_changed(http):
    http.queue = [reply(200, {"request_uuid": "x"})]
    run(VOBIZ.place_call({**VB, "base_url": "https://eu.vobiz.example/api/v1/"}, TO, WSS, STATUS, ANSWER))
    assert http.sent[0]["url"] == "https://eu.vobiz.example/api/v1/Account/VBID/Call/"


def test_a_429_is_asked_once_more_and_other_refusals_are_not(http):
    http.queue = [reply(429, {"error": "slow"}), reply(200, {"request_uuid": "vb-2"})]
    assert run(VOBIZ.place_call(VB, TO, WSS, STATUS, ANSWER)) == "vb-2" and len(http.sent) == 2
    http.sent.clear()
    http.queue = [reply(429, {"error": "slow"}), reply(429, {"error": "still slow"})]
    with pytest.raises(ProviderHTTPError) as e:
        run(VOBIZ.place_call(VB, TO, WSS, STATUS, ANSWER))
    assert e.value.status == 429 and len(http.sent) == 2
    http.sent.clear()
    http.queue = [reply(402, {"error": "Insufficient balance"})]
    with pytest.raises(ProviderHTTPError):
        run(VOBIZ.place_call(VB, TO, WSS, STATUS, ANSWER))
    assert len(http.sent) == 1


def test_a_network_error_is_unreachable(http):
    http.boom = True
    with pytest.raises(ProviderUnreachable):
        run(VOBIZ.place_call(VB, TO, WSS, STATUS, ANSWER))
    assert run(VOBIZ.hangup(VB, "vb-1")) is False                     # best effort: never raises


def test_hangup_is_a_delete(http):
    http.queue = [reply(204, text="")]
    assert run(VOBIZ.hangup(VB, "vb-1")) is True
    assert http.sent[0]["method"] == "delete" and http.sent[0]["url"] == "https://api.vobiz.ai/api/v1/Account/VBID/Call/vb-1/"
    http.queue = [reply(404, {})]
    assert run(VOBIZ.hangup(VB, "vb-1")) is False


def test_status_reads_the_live_call_then_the_record_then_says_queued(http):
    http.queue = [reply(200, {"call_status": "in-progress", "session_start": "2026-11-04 10:00:00"})]
    s = run(VOBIZ.call_status(VB, "u"))
    assert s["status"] == "in-progress" and s["started_at"] == "2026-11-04 10:00:00"
    assert http.sent[0]["url"].endswith("/Call/u/?status=live")
    http.queue = [reply(404, {"error": "not found"}), reply(200, {"duration": 46, "billsec": 30, "answer_time": "t0",
                                                                  "end_time": "t1", "hangup_cause_name": "Normal Hangup"})]
    s = run(VOBIZ.call_status(VB, "u"))
    assert s["status"] == "completed" and s["duration_s"] == 30 and s["ended_at"] == "t1" and s["started_at"] == "t0"
    http.queue = [reply(404, {}), reply(200, {"duration": 3, "billsec": 0, "hangup_cause_name": "Busy Line"})]
    assert run(VOBIZ.call_status(VB, "u"))["status"] == "busy"
    http.queue = [reply(404, {}), reply(200, {"data": {"duration": 8, "billsec": 0, "hangup_cause_name": "Timeout"}})]
    assert run(VOBIZ.call_status(VB, "u"))["status"] == "no-answer"      # a {success, data} envelope is unwrapped
    http.queue = [reply(404, {}), reply(404, {})]
    assert run(VOBIZ.call_status(VB, "u"))["status"] == "queued"
    http.queue = [reply(500, {"error": "boom"})]
    with pytest.raises(ProviderHTTPError):
        run(VOBIZ.call_status(VB, "u"))


@pytest.mark.parametrize("status,body,kind", [
    (401, {"error": "Authentication credentials are not valid"}, "auth_failed"),
    (402, {"error": "Account balance is too low to place the call"}, "exhausted"),
    (400, {"error": "Insufficient balance"}, "exhausted"),
    (400, {"error": "From number not owned by account"}, "not_allowed"),
    (400, {"error": "Destination is not a sandbox number"}, "not_allowed"),
    (429, {"error": "Concurrent call limit exceeded"}, "rate_limited"),
    (400, {"error": "Invalid destination"}, None),
    (500, {"message": "boom"}, None),
])
def test_refusals(status, body, kind):
    assert VOBIZ.classify_refusal(status, json.dumps(body)) == kind


def test_hangup_callbacks_read_vobizs_fields():
    # https://www.vobiz.ai/docs/xml/request.md: CallUUID, CallStatus, Duration, BillDuration, HangupCause
    cb = VOBIZ.parse_status_callback({"CallUUID": "u1", "CallStatus": "completed", "Duration": "12", "BillDuration": "9",
                                      "HangupCause": "NORMAL_CLEARING"})
    assert cb == {"sid": "u1", "status": "completed", "duration_s": 9, "ended_at": None, "message": "NORMAL_CLEARING"}
    assert VOBIZ.parse_status_callback({"CallUUID": "u3", "CallStatus": "timeout"})["status"] == "no-answer"
    assert VOBIZ.parse_status_callback({"CallUUID": "u2", "Event": "Hangup", "Duration": "7"})["status"] == "completed"
    assert VOBIZ.parse_status_callback({"CallStatus": "completed"}) is None            # names no call


def test_the_answer_document_is_a_bidirectional_mulaw_stream_and_may_dial_on_afterwards():
    assert stream_xml(WSS) == ('<?xml version="1.0" encoding="UTF-8"?><Response><Stream bidirectional="true" keepCallAlive="true" '
                               'contentType="audio/x-mulaw;rate=8000">' + WSS + "</Stream></Response>")
    assert VOBIZ.answer_document(WSS) == stream_xml(WSS, None, None)
    doc = stream_xml(WSS + "?a=1&b=2", "+919876500000", "+912240000000")
    assert "a=1&amp;b=2" in doc                                                   # escaped
    assert doc.endswith('</Stream><Dial callerId="+912240000000" timeout="30"><Number>+919876500000</Number></Dial></Response>')


def test_the_missed_call_document_rejects_before_answering():
    assert reject_xml() == '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup reason="rejected"/></Response>'


def test_the_account_comes_from_three_settings(monkeypatch):
    for n in ("VOBIZ_AUTH_ID", "VOBIZ_AUTH_TOKEN", "VOBIZ_CALLER_ID", "VOBIZ_BASE_URL", "PUBLIC_BASE_URL", "PHONE_PROVIDERS"):
        monkeypatch.delenv(n, raising=False)
    assert registry.account() is None and VOBIZ.missing_vars() == ["VOBIZ_AUTH_ID", "VOBIZ_AUTH_TOKEN", "VOBIZ_CALLER_ID"]
    assert registry.missing_settings() == ["VOBIZ_AUTH_ID", "VOBIZ_AUTH_TOKEN", "VOBIZ_CALLER_ID", "PUBLIC_BASE_URL"]
    monkeypatch.setenv("VOBIZ_AUTH_ID", "id")
    monkeypatch.setenv("VOBIZ_AUTH_TOKEN", "tok")
    monkeypatch.setenv("VOBIZ_CALLER_ID", "+912240000000")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://x.example")
    assert registry.account() == {"auth_id": "id", "token": "tok", "caller_id": "+912240000000",
                                  "base_url": "https://api.vobiz.ai/api/v1"}
    assert registry.missing_settings() == []


def test_phone_providers_defaults_to_vobiz_and_ignores_every_other_name(monkeypatch, caplog):
    monkeypatch.delenv("PHONE_PROVIDERS", raising=False)
    registry._warned.clear()
    assert registry.enabled() == ["vobiz"]
    monkeypatch.setenv("PHONE_PROVIDERS", "twilio, vobiz, vobiz, ")
    assert registry.enabled() == ["vobiz"]
    assert sum("twilio" in r.getMessage() for r in caplog.records) == 1
    monkeypatch.setenv("PHONE_PROVIDERS", "twilio")
    assert registry.enabled() == ["vobiz"]                                     # nothing usable left: the default
    with pytest.raises(KeyError):
        registry.provider("twilio")


def test_the_provider_names_itself_and_says_it_has_not_been_run_for_real():
    assert registry.NAMES == ("vobiz",) and VOBIZ.name == "vobiz" and VOBIZ.display == "Vobiz"
    assert VOBIZ.verified is False and "Not yet run against a real account" in VOBIZ.verification_note
    assert VOBIZ.api_hangup is True
