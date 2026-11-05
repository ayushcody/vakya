"""The Cloud API client (services/whatsapp.py), the owner's routes and the hang-up hooks of the phone and web call paths."""
import asyncio

import pytest

from routers import phone as px
from routers import web_call
from services import booking_notify as bn
from services import call_records, whatsapp
from tests.test_offline_agents import LOCAL, env, make_agent  # noqa: F401  (env is a fixture)
from tests.test_phone import rig  # noqa: F401
from tests.test_phone import vobiz  # noqa: F401
from tests.test_caller_profiles import phone_call
from services import web_session

TOKEN = "EAAB-test-token-1234567890"


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "100200300")


def test_indian_numbers_become_91_form():
    assert whatsapp.to_e164("9876543210") == "919876543210"
    assert whatsapp.to_e164("+91 98765 43210") == "919876543210" and whatsapp.to_e164("09876543210") == "919876543210"
    with pytest.raises(whatsapp.WhatsAppError):
        whatsapp.to_e164("12345")


def test_status_never_has_the_token(cfg):
    s = whatsapp.status()
    assert s["configured"] and s["access_token_set"] and TOKEN not in str(s)
    assert s["templates"] == {"booked": "vakya_booking_confirmed", "moved": "vakya_booking_moved",
                              "cancelled": "vakya_booking_cancelled"}


def test_send_template_body_shape_and_message_id(cfg, monkeypatch):
    seen = {}

    def http(method, url, *, headers=None, json_body=None):
        seen.update(method=method, url=url, body=json_body)
        return 200, {"messages": [{"id": "wamid.ABC"}]}
    monkeypatch.setattr(whatsapp, "_http", http)
    assert whatsapp.send_template("9876543210", "vakya_booking_moved", "mr", ["a", "b"]) == "wamid.ABC"
    assert seen["method"] == "POST" and seen["body"]["type"] == "template" and seen["body"]["messaging_product"] == "whatsapp"
    assert seen["body"]["template"] == {"name": "vakya_booking_moved", "language": {"code": "mr"},
                                        "components": [{"type": "body", "parameters": [{"type": "text", "text": "a"},
                                                                                       {"type": "text", "text": "b"}]}]}


def test_meta_errors_carry_code_and_message(cfg, monkeypatch):
    monkeypatch.setattr(whatsapp, "_http", lambda *a, **k: (400, {"error": {"message": "Template does not exist", "code": 132001}}))
    with pytest.raises(whatsapp.WhatsAppError) as e:
        whatsapp.send_template("9876543210", "x", "en", [])
    assert e.value.code == 132001 and "Template does not exist" in str(e.value)


def test_unconfigured_send_raises_without_calling_meta():
    with pytest.raises(whatsapp.WhatsAppError):
        whatsapp.send_template("9876543210", "x", "en", [])


# ------------------------------------------------------------------------------------------------ routes

def test_routes_show_status_and_masked_messages(app_client, env):
    bn.bind_call("c1")
    bn.booking_changed("booked", {"booking_id": "A1", "name": "Ravi", "phone": "9876543210", "date": "2026-11-06",
                                  "time": "15:00", "version": 1})
    bn.record_consent(True)
    bn.call_ended("c1")
    import time
    bn.flush_due(time.time() + 31)
    s = app_client.get("/api/whatsapp/status", headers=LOCAL).json()
    assert s["configured"] is False and s["missing"] == ["WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"]
    assert s["counts"]["preview"] == 1 and s["templates"]["booked"] == "vakya_booking_confirmed"
    m = app_client.get("/api/whatsapp/messages", headers=LOCAL).json()["messages"]
    assert m[0]["state"] == "preview" and m[0]["phone_masked"] == "••••3210" and "9876543210" not in str(m)


# ------------------------------------------------------------------------------------------------ hang-up hooks

def test_the_stream_closing_triggers_the_flush(rig, env, monkeypatch):
    ended = []
    monkeypatch.setattr(bn, "call_ended", lambda cid: ended.append(cid) or 0)
    a = make_agent(rig.client)
    sess = phone_call(rig, a)
    assert ended == [sess.conversation_id] and sess.conversation_id


def test_the_vobiz_hangup_callback_triggers_the_flush(monkeypatch):
    ended = []
    monkeypatch.setattr(bn, "call_ended", lambda cid: ended.append(cid) or 0)
    call_records.upsert("sid-1", owner="u1", conversation_id="conv-9", status="in-progress")
    claims = {"u": "u1", "a": "agent-1"}
    assert px._record_status(claims, {"sid": "sid-1", "status": "in-progress"}) is True
    assert ended == []
    assert px._record_status(claims, {"sid": "sid-1", "status": "completed", "duration_s": 5}) is True
    assert ended == ["conv-9"]


def test_the_web_call_ending_triggers_the_flush(monkeypatch):
    ended = []
    monkeypatch.setattr(bn, "call_ended", lambda cid: ended.append(cid) or 0)
    web_session.VISITOR_CONV["vis-1"] = "conv-web"
    asyncio.run(web_call._remember_web_caller("link-1", {"v": "vis-1", "m": "local"}))
    assert ended == ["conv-web"]


def test_a_real_hangup_after_a_change_sends_one_message(rig, env, monkeypatch):
    """Whole path: a change bound to the call, the stream closes, the (short) delay passes, one preview row exists."""
    monkeypatch.setenv("WHATSAPP_SEND_DELAY_S", "0.1")
    a = make_agent(rig.client)

    def change(sess):
        bn.bind_call(sess.conversation_id)
        assert bn.booking_changed("booked", {"booking_id": "A1", "name": "Ravi", "phone": "9876543210", "date": "2026-11-06",
                                             "time": "15:00", "version": 1, "agent_id": a["id"]})["status"] == "ask"
        bn.record_consent(True)
    phone_call(rig, a, then=change)
    import time
    end = time.time() + 3
    while not bn.recent_messages() and time.time() < end:
        time.sleep(0.02)
    assert [m["state"] for m in bn.recent_messages()] == ["preview"]
