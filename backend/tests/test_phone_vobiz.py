"""What is specific to Vobiz in the phone router (routers/phone.py): the answer document and its signed token, how an inbound
caller's number gets from the answer request to the call, the raw frames on the wire, the API hangup after the agent ends a
call, the flow-mode transfer XML, and the token boundaries between stream, answer, status and missed-call routes. Same fakes
as test_phone.py: no model, no network, no real call."""
import json
import re
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import phone_public
from routers import phone as px
from services import call_control, call_records
from tests.test_offline_agents import LOCAL, env, make_agent  # noqa: F401  (env is a fixture)
from tests.test_phone import (STREAM_SID, FakeHttp, call, configure, feed, is_mark, pcm_silence, pcm_tone,  # noqa: F401
                              read_until, rig, start_msg, token_for, vobiz)


def answer_token(agent, user="local-user", origin=None, ttl=300):
    return px.make_token(agent["id"], user, "local", ttl, kind="ans", origin=origin)


def fetch_answer(rig, agent, **form):
    r = rig.client.post(f"{px.ANSWER_PATH}/{answer_token(agent)}", data=form)
    assert r.status_code == 200, r.text
    return r


def stream_of(xml):
    return re.search(r"<Stream[^>]*>(wss://[^<]+)</Stream>", xml).group(1)


# ------------------------------------------------------------------------------------------------ the answer document

def test_the_answer_document_connects_the_call_to_a_fresh_stream_token(rig, monkeypatch):
    configure(monkeypatch)
    a = make_agent(rig.client)
    r = fetch_answer(rig, a, CallUUID="uuid-1", From="+919876543210", To="+912269000001", Direction="inbound")
    assert r.headers["content-type"].startswith("application/xml")
    assert 'bidirectional="true"' in r.text and 'contentType="audio/x-mulaw;rate=8000"' in r.text and "<Dial" not in r.text
    wss = stream_of(r.text)
    assert wss.startswith("wss://abc.ngrok.app/api/phone/stream/")
    claims = px.read_token(wss.rsplit("/", 1)[1])
    assert claims["a"] == a["id"] and "k" not in claims and claims["x"] < time.time() + 16 * 60   # short-lived, opens the stream


def test_the_answer_works_for_a_get_too_and_needs_the_public_address(rig, monkeypatch):
    configure(monkeypatch)
    a = make_agent(rig.client)
    assert rig.client.get(f"{px.ANSWER_PATH}/{answer_token(a)}").status_code == 200
    monkeypatch.delenv("PUBLIC_BASE_URL")
    assert rig.client.get(f"{px.ANSWER_PATH}/{answer_token(a)}").status_code == 503


def test_tokens_do_not_cross_routes(rig, monkeypatch):
    configure(monkeypatch)
    a = make_agent(rig.client)
    stream = px.make_token(a["id"], "local-user", "local", 300)
    ans = answer_token(a)
    missed = px.make_token(a["id"], "local-user", "local", 300, kind="missed")
    assert rig.client.post(f"{px.ANSWER_PATH}/{stream}").status_code == 404           # a stream token fetches no answer
    assert rig.client.post(f"{px.ANSWER_PATH}/{missed}").status_code == 404
    assert rig.client.post(f"{px.ANSWER_PATH}/not-a-token").status_code == 404
    for token in (ans, missed):                                                         # and these two open no stream
        with pytest.raises(WebSocketDisconnect) as e:
            with rig.client.websocket_connect(f"{px.STREAM_PATH}/{token}"):
                pass
        assert e.value.code == 4401


def test_flow_transfer_adds_a_dial_after_the_stream_with_the_agents_number(rig, tmp_path, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("VAKYA_CALL_SETTINGS_FILE", str(tmp_path / "call_settings.json"))
    a = make_agent(rig.client)
    rig.client.put(f"/api/agents/{a['id']}/call-settings", headers=LOCAL, json={"transfer_number": "+919876500000"})
    assert "<Dial" not in fetch_answer(rig, a).text                                    # callback mode (default): no Dial
    monkeypatch.setenv("PHONE_TRANSFER_MODE", "flow")
    xml = fetch_answer(rig, a).text
    assert xml.endswith('</Stream><Dial callerId="+912269000001" timeout="30"><Number>+919876500000</Number></Dial></Response>')
    other = make_agent(rig.client)                                                     # an agent with no transfer number
    assert "<Dial" not in fetch_answer(rig, other).text


# ------------------------------------------------------------------------------------------------ who is calling

def test_an_inbound_callers_number_comes_from_the_answer_request_not_the_stream(rig, monkeypatch):
    configure(monkeypatch)
    a = make_agent(rig.client)
    fetch_answer(rig, a, CallUUID="uuid-in-1", From="+91 98765 43210", To="+912269000001", Direction="inbound")
    assert px._inbound_callers == {"uuid-in-1": "+919876543210"}
    with rig.client.websocket_connect(f"{px.STREAM_PATH}/{token_for(a)}") as ws:
        ws.send_text(start_msg(call_id="uuid-in-1", caller=""))                         # Vobiz's start has no from/to
        read_until(ws, is_mark)
        sess = call_control.live_sessions()[0]
        assert sess.party == "+919876543210"
        ws.send_text(json.dumps({"event": "stop"}))
    assert call_records.get("uuid-in-1")["direction"] == "inbound"


def test_an_outbound_answer_request_is_not_taken_for_a_callers_number(rig, monkeypatch):
    configure(monkeypatch)
    a = make_agent(rig.client)
    fetch_answer(rig, a, CallUUID="uuid-out", From="+912269000001", To="+919876543210", Direction="outbound")
    assert px._inbound_callers == {}


def test_the_caller_register_forgets_the_oldest(rig, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(px, "KNOWN_CALLS_MAX", 2)
    a = make_agent(rig.client)
    for n in range(3):
        fetch_answer(rig, a, CallUUID=f"u{n}", From="+919876543210", Direction="inbound")
    assert list(px._inbound_callers) == ["u1", "u2"]


def test_an_outbound_call_is_recognised_by_the_call_id_vobiz_returns(rig, vobiz, monkeypatch):
    configure(monkeypatch)
    a = make_agent(rig.client)
    vobiz.body = {"message": "Call fired", "request_uuid": "uuid-out-7"}
    assert call(rig, a).json()["call_sid"] == "uuid-out-7"
    with rig.client.websocket_connect(f"{px.STREAM_PATH}/{token_for(a)}") as ws:
        ws.send_text(start_msg(call_id="uuid-out-7", caller=""))      # request_uuid is "equivalent to call_uuid" (make-call.md)
        read_until(ws, is_mark)
        sess = call_control.live_sessions()[0]
        assert sess.party == "+919876543210" and sess.outbound is True
        ws.send_text(json.dumps({"event": "stop"}))
    assert call_records.get("uuid-out-7")["direction"] == "outbound"


# ------------------------------------------------------------------------------------------------ on the wire

def test_the_greeting_goes_out_as_playaudio_frames_ending_in_a_checkpoint(rig):
    a = make_agent(rig.client)
    with rig.client.websocket_connect(f"{px.STREAM_PATH}/{token_for(a)}") as ws:
        ws.send_text(start_msg())
        msgs = read_until(ws, is_mark)
    raw = [m["raw"] for m in msgs]
    assert {m["event"] for m in raw} == {"playAudio", "checkpoint"}
    assert all(m["streamId"] == STREAM_SID for m in raw)
    assert raw[-1] == {"event": "checkpoint", "streamId": STREAM_SID, "name": "turn-1-end"}
    assert all(set(m["media"]) == {"contentType", "sampleRate", "payload"} for m in raw if m["event"] == "playAudio")


def test_vobiz_closing_the_stream_ends_the_call_without_any_stop_event(rig):
    a = make_agent(rig.client)
    with rig.client.websocket_connect(f"{px.STREAM_PATH}/{token_for(a)}") as ws:
        ws.send_text(start_msg())
        read_until(ws, is_mark)
    time.sleep(0.4)
    assert call_records.get("CA1")["hangup_reason"] == "caller_hung_up" and not call_control.live_sessions()


def test_the_played_stream_event_is_the_agents_mark(rig):
    a = make_agent(rig.client)
    with rig.client.websocket_connect(f"{px.STREAM_PATH}/{token_for(a)}") as ws:
        ws.send_text(start_msg())
        read_until(ws, is_mark)
        sess = call_control.live_sessions()[0]
        sess.pacer.play_end = sess.pacer.clock() + 30                       # the pacer thinks 30 s of audio is still queued
        ws.send_text(json.dumps({"event": "playedStream", "name": "turn-1-end"}))
        deadline = time.time() + 3
        while sess.pacer.play_end > sess.pacer.clock() + 1 and time.time() < deadline:
            time.sleep(0.02)
        assert sess.pacer.play_end <= sess.pacer.clock() + 1


# ------------------------------------------------------------------------------------------------ hanging up by API

@pytest.mark.asyncio
@pytest.mark.parametrize("reason,expected", [("ended_by_agent", True), ("voicemail", True), ("time_limit", True),
                                              ("callback_promised", True), ("transferred", False),
                                              ("caller_hung_up", False), (None, False)])
async def test_the_agent_ending_a_call_hangs_it_up_through_the_api(vobiz, monkeypatch, reason, expected):
    configure(monkeypatch)
    s = px.CallSession.__new__(px.CallSession)
    s.provider_name, s.call_sid, s.end_reason, s.log = "vobiz", "uuid-9", reason, []
    await s._api_hangup()
    if expected:
        assert vobiz.deleted[0]["url"] == "https://api.vobiz.ai/api/v1/Account/MA_acme/Call/uuid-9/"
        assert vobiz.deleted[0]["headers"]["X-Auth-Token"] == "tok"
        assert s.log == [{"event": "api_hangup"}]
    else:
        assert vobiz.deleted == []


@pytest.mark.asyncio
async def test_without_settings_no_hangup_request_is_made_and_nothing_raises(vobiz, monkeypatch):
    for n in ("VOBIZ_AUTH_ID", "VOBIZ_AUTH_TOKEN", "VOBIZ_CALLER_ID"):
        monkeypatch.delenv(n, raising=False)
    s = px.CallSession.__new__(px.CallSession)
    s.provider_name, s.call_sid, s.end_reason, s.log = "vobiz", "uuid-9", "ended_by_agent", []
    await s._api_hangup()
    assert vobiz.deleted == []


# ------------------------------------------------------------------------------------------------ the public app

def test_the_public_app_serves_the_answer_document_for_a_good_token(rig, monkeypatch):
    configure(monkeypatch)
    a = make_agent(rig.client)
    with TestClient(phone_public.public_app) as c:
        r = c.post(f"{px.ANSWER_PATH}/{answer_token(a)}", data={"CallUUID": "u", "From": "+919876543210", "Direction": "inbound"})
    assert r.status_code == 200 and "<Stream" in r.text


def test_the_public_status_route_files_a_hangup_under_the_token_owner(rig, vobiz, monkeypatch):
    configure(monkeypatch)
    a = make_agent(rig.client)
    call(rig, a)
    token = vobiz.sent[0]["json"]["hangup_url"].rsplit("/", 1)[1]
    with TestClient(phone_public.public_app) as c:
        assert c.post(f"{px.STATUS_CALLBACK_PATH}/{token}", data={"CallUUID": "uuid-abc123", "CallStatus": "no-answer"}
                      ).json() == {"ok": True}
    assert call_records.get("uuid-abc123")["status"] == "no-answer"
