"""The missed-call callback on Vobiz: a number whose application has /api/phone/missed-call/{token} as its Answer URL is
answered with <Hangup reason="rejected"/> (never answered, so never billed) and the receptionist rings the caller back.
The outbound call is faked everywhere (no number is ever called)."""
import asyncio
import re
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import phone_public
from routers import phone as px
from services import call_control, missed_calls
from tests.test_offline_agents import LOCAL, env, make_agent  # noqa: F401  (env is a fixture)
from tests.test_phone import CONFIG, FakeHttp, rig, vobiz  # noqa: F401  (fixtures)

ALLOWED = "+919876500001"          # a made-up number
CALLER = "+919876500001"           # the same, as Vobiz sends it (From)
STRANGER = "+919876500002"


class Recorder:
    """Stands in for phone.dial: records the call it would place."""

    def __init__(self):
        self.calls = []
        self.fail = None

    async def __call__(self, b, agent, user_id, mode, to, origin=None):
        if self.fail:
            raise self.fail
        self.calls.append({"agent": agent["id"], "to": to, "origin": origin, "user": user_id, "mode": mode})
        return {"status": "calling", "to": to, "call_sid": f"uuid-fake{len(self.calls)}", "agent_id": agent["id"]}


@pytest.fixture()
def hook(rig, monkeypatch, tmp_path):
    for k, v in CONFIG.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PHONE_ALLOWED_NUMBERS", ALLOWED)
    monkeypatch.setenv("VAKYA_MISSED_CALLS_FILE", str(tmp_path / "missed_calls.jsonl"))
    monkeypatch.setenv("MISSED_CALL_DELAY_S", "0")
    monkeypatch.delenv("MISSED_CALL_CALLBACK", raising=False)
    monkeypatch.delenv("MISSED_CALL_DEDUPE_S", raising=False)
    missed_calls.reset()
    px._missed_tasks.clear()
    rec = Recorder()
    real_dial = px.dial
    monkeypatch.setattr(px, "dial", rec)
    agent = make_agent(rig.client)
    token = px.make_token(agent["id"], "local-user", "local", 3600, kind="missed")
    return SimpleNamespace(rig=rig, agent=agent, token=token, rec=rec, monkeypatch=monkeypatch, real_dial=real_dial)


REJECT = '<Hangup reason="rejected"/>'


def hit(h, number=CALLER, to="+912269000001", token=None, method="post", **extra):
    """One request to the phone-only public app (what the tunnel exposes), as Vobiz makes it to an Answer URL (a form POST:
    CallUUID, From, To, Direction, CallStatus), on a loop that outlives the request. Whatever happens, the answer is the
    XML that rejects the call."""
    path = f"{px.MISSED_CALL_PATH}/{token or h.token}"
    params = {"CallUUID": "vz-uuid-1", "From": number, "To": to, "Direction": "inbound", "CallStatus": "ringing", **extra}
    with TestClient(phone_public.public_app) as c:
        r = c.get(path, params=params) if method == "get" else c.post(path, data=params)
        assert r.status_code == 200 and REJECT in r.text and r.headers["content-type"].startswith("application/xml"), r.text
        deadline = time.time() + 3
        while px._missed_tasks and time.time() < deadline:
            time.sleep(0.02)
        return r


def events(h):
    return list(reversed(missed_calls.list_for("local-user", 100)))     # oldest first


def verdict(h):
    """What the last missed call came to, from the event log: scheduled (called back or about to be) or skipped with why."""
    last = events(h)[-1]
    if last["status"] == "skipped":
        return {"status": "skipped", "reason": last["reason"]}
    return {"status": "scheduled"}


def statuses(h):
    return [(e["status"], e.get("reason")) for e in events(h)]


def test_an_allowed_number_is_called_back_with_the_origin_marked(hook):
    hit(hook)
    assert verdict(hook) == {"status": "scheduled"}
    assert hook.rec.calls == [{"agent": hook.agent["id"], "to": ALLOWED, "origin": "missed_call", "user": "local-user",
                               "mode": "local"}]
    assert statuses(hook) == [("received", None), ("called_back", None)]
    done = events(hook)[-1]
    assert done["callback_sid"] == "uuid-fake1" and done["call_sid"] == "vz-uuid-1" and done["agent_id"] == hook.agent["id"]
    assert ALLOWED not in str(events(hook)) and "+91•••••00001" in str(events(hook))   # numbers are stored masked only


def test_a_get_works_like_a_post(hook):
    hit(hook, method="get")                      # an application whose answer method is GET sends the same fields in the query
    assert len(hook.rec.calls) == 1


def test_a_number_not_in_the_allow_list_is_refused(hook):
    hit(hook, number=STRANGER)
    assert verdict(hook) == {"status": "skipped", "reason": "not_allowed"}
    assert hook.rec.calls == []
    assert statuses(hook) == [("received", None), ("skipped", "not_allowed")]


def test_without_an_allow_list_any_valid_number_is_called(hook):
    hook.monkeypatch.setenv("PHONE_ALLOWED_NUMBERS", "")
    hit(hook, number=STRANGER)
    assert verdict(hook)["status"] == "scheduled" and hook.rec.calls[0]["to"] == "+919876500002"


@pytest.mark.parametrize("number", ["", "12", "not-a-number"])
def test_a_missing_or_invalid_caller_number_is_skipped(hook, number):
    hook.monkeypatch.setenv("PHONE_ALLOWED_NUMBERS", "")
    hit(hook, number=number)
    assert verdict(hook)["reason"] == "bad_number" and hook.rec.calls == []


def test_at_most_one_callback_per_number_per_ten_minutes(hook):
    hit(hook)
    assert verdict(hook)["status"] == "scheduled"
    hit(hook)
    assert verdict(hook)["reason"] == "recent_callback"
    assert len(hook.rec.calls) == 1
    # a different number is not held back
    hook.monkeypatch.setenv("PHONE_ALLOWED_NUMBERS", "")
    hit(hook, number=STRANGER)
    assert verdict(hook)["status"] == "scheduled"
    assert len(hook.rec.calls) == 2


def test_the_dedupe_window_is_ten_minutes_by_default_and_then_opens():
    missed_calls.reset()
    assert missed_calls.dedupe_s() == 600
    assert missed_calls.claim("+919876500001", now=1000.0) is True
    assert missed_calls.claim("+919876500001", now=1599.0) is False       # 599 s later: too soon
    assert missed_calls.claim("+919876500001", now=1600.1) is True        # more than 10 minutes later
    missed_calls.reset()


def test_never_while_a_call_with_that_number_is_live(hook):
    session = SimpleNamespace(party=ALLOWED, conversation_id="c1")
    call_control.register("c1", session)
    try:
        hit(hook)
    finally:
        call_control.unregister("c1")
    assert verdict(hook)["reason"] == "call_live" and hook.rec.calls == []
    # the number is not held for ten minutes because of it: with the line free, the next hit calls back
    hit(hook)
    assert verdict(hook)["status"] == "scheduled"


def test_a_call_we_placed_that_has_not_ended_counts_as_live(hook, tmp_path):
    from services import call_records
    px._known_calls["CAlive"] = {"owner": "local-user", "party": ALLOWED}
    call_records.upsert("CAlive", status="ringing")
    assert px._number_is_live(ALLOWED) is True
    call_records.upsert("CAlive", status="completed")
    assert px._number_is_live(ALLOWED) is False


def test_the_agent_of_the_link_places_the_call(hook):
    second = make_agent(hook.rig.client)
    token = px.make_token(second["id"], "local-user", "local", 3600, kind="missed")
    hit(hook, token=token)
    assert hook.rec.calls[0]["agent"] == second["id"]


def test_a_deleted_agent_falls_back_to_the_first_receptionist(hook):
    ghost = px.make_token("deleted-agent", "local-user", "local", 3600, kind="missed")
    hit(hook, token=ghost)
    assert hook.rec.calls[0]["agent"] == hook.agent["id"]     # the only (so first) agent of the owner


def test_with_no_agent_at_all_it_is_skipped(hook):
    from core.runtime import runtime as backend
    backend.local_db.delete_agent(hook.agent["id"], "local-user")
    hit(hook)
    assert verdict(hook)["reason"] == "no_agent" and hook.rec.calls == []
    assert missed_calls.claim(ALLOWED) is True            # the number was not held


def test_the_callback_waits_for_the_delay(hook):
    hook.monkeypatch.setenv("MISSED_CALL_DELAY_S", "0.4")
    with TestClient(phone_public.public_app) as c:
        t0 = time.time()
        r = c.post(f"{px.MISSED_CALL_PATH}/{hook.token}", data={"CallUUID": "s", "From": CALLER, "To": "x"})
        assert r.status_code == 200 and REJECT in r.text and time.time() - t0 < 0.3     # rejected at once
        assert hook.rec.calls == []                       # nothing placed yet
        deadline = time.time() + 3
        while not hook.rec.calls and time.time() < deadline:
            time.sleep(0.05)
    assert len(hook.rec.calls) == 1 and time.time() - t0 >= 0.4


def test_the_default_delay_is_eight_seconds(monkeypatch):
    monkeypatch.delenv("MISSED_CALL_DELAY_S", raising=False)
    assert missed_calls.delay_s() == 8.0


def test_switched_off_it_calls_nobody(hook):
    hook.monkeypatch.setenv("MISSED_CALL_CALLBACK", "off")
    hit(hook)
    assert verdict(hook)["reason"] == "disabled" and hook.rec.calls == []
    assert statuses(hook) == [("received", None), ("skipped", "disabled")]


def test_a_failed_outbound_call_is_logged_and_vobiz_still_gets_the_reject(hook):
    hook.rec.fail = px.AppError("VOBIZ_REFUSED", "Vobiz refused the call (HTTP 403): not allowed", 502)
    hit(hook)
    assert statuses(hook) == [("received", None), ("failed", "vobiz_refused")]
    assert "refused" in events(hook)[-1]["message"]


def test_an_unexpected_error_in_the_callback_is_logged_as_failed(hook):
    hook.rec.fail = RuntimeError("boom")
    hit(hook)
    assert statuses(hook)[-1] == ("failed", "error")


def test_the_phone_must_be_set_up(hook):
    hook.monkeypatch.delenv("VOBIZ_AUTH_ID", raising=False)
    hit(hook)
    assert verdict(hook)["reason"] == "not_configured" and hook.rec.calls == []


def test_too_many_calls_an_hour_are_refused(hook):
    hook.monkeypatch.setenv("PHONE_ALLOWED_NUMBERS", "")
    now = time.time()
    px._calls_by_owner["local-user"].extend([now] * px.CALLS_PER_HOUR)
    hit(hook)
    assert verdict(hook)["reason"] == "rate_limited" and hook.rec.calls == []


@pytest.mark.parametrize("token", ["nonsense", "", "a.b"])
def test_a_bad_token_is_still_rejected_unanswered_and_does_nothing(hook, token):
    hit(hook, token=token or "x")
    assert hook.rec.calls == []
    assert events(hook) == []


def test_only_a_missed_call_token_works_here_and_it_opens_nothing_else(hook):
    stream = px.make_token(hook.agent["id"], "local-user", "local", 3600)             # an ordinary stream token
    hit(hook, token=stream)
    assert hook.rec.calls == [] and events(hook) == []
    claims = px.read_token(hook.token)
    assert claims["k"] == "missed"                                                     # the stream and status routes refuse "k"
    r = TestClient(phone_public.public_app).post(px.STATUS_CALLBACK_PATH + "/" + hook.token, data={"CallUUID": "x"})
    assert r.json() == {"ok": False}


def test_the_route_is_on_the_phone_only_app_and_listed_in_the_health_probe(hook):
    c = TestClient(phone_public.public_app)
    assert px.MISSED_CALL_PATH + "/{token}" in c.get("/api/phone/public/health").json()["routes"]
    assert REJECT in c.get(px.MISSED_CALL_PATH + "/" + hook.token, params={"From": CALLER}).text


def test_the_owner_gets_a_link_and_the_event_list(hook):
    r = hook.rig.client.post("/api/phone/missed-call-link", headers=LOCAL, json={"agent_id": hook.agent["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"].startswith("https://abc.ngrok.app/api/phone/missed-call/") and body["enabled"] is True
    assert body["method"] == "POST" and "Answer URL" in body["setup"] and "not charged" in body["setup"]
    token = body["url"].rsplit("/", 1)[1]
    assert px.read_token(token)["k"] == "missed" and px.read_token(token)["a"] == hook.agent["id"]
    assert body["valid_days"] >= 300 and body["delay_s"] == 0.0
    hit(hook, token=token)
    listing = hook.rig.client.get("/api/phone/missed-calls", headers=LOCAL).json()
    assert listing["enabled"] is True
    assert [e["status"] for e in listing["events"]] == ["called_back", "received"]           # newest first
    assert listing["events"][0]["hit"] == listing["events"][1]["hit"]


def test_the_link_needs_the_public_address(hook):
    hook.monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    r = hook.rig.client.post("/api/phone/missed-call-link", headers=LOCAL, json={"agent_id": hook.agent["id"]})
    assert r.status_code == 503 and r.json()["code"] == "PHONE_NOT_CONFIGURED"


def test_events_are_per_owner(hook):
    hit(hook)
    assert missed_calls.list_for("someone-else") == []


def test_dial_carries_the_origin_into_the_answer_and_stream_tokens(hook, vobiz):
    """dial() itself (not faked): the call is placed through Vobiz's API with an answer URL whose token says missed_call."""
    hook.monkeypatch.setattr(px, "dial", hook.real_dial)
    agent = hook.rig.env.local_db.get("agents", hook.agent["id"], "local-user")
    out = asyncio.run(hook.real_dial(hook.rig.env, agent, "local-user", "local", "+919876500001", origin="missed_call"))
    assert out["call_sid"] == "uuid-abc123"
    body = FakeHttp.sent[0]["json"]
    assert px.read_token(body["answer_url"].rsplit("/", 1)[1])["o"] == "missed_call"
    plain = asyncio.run(hook.real_dial(hook.rig.env, agent, "local-user", "local", "+919876500001"))
    assert "o" not in px.read_token(FakeHttp.sent[1]["json"]["answer_url"].rsplit("/", 1)[1])
    # the answer URL Vobiz fetches turns that into a stream token that still says so
    r = hook.rig.client.post(body["answer_url"].replace("https://abc.ngrok.app", ""), data={"CallUUID": out["call_sid"]})
    assert r.status_code == 200
    stream = re.search(r">(wss://[^<]+)<", r.text).group(1)
    assert px.read_token(stream.rsplit("/", 1)[1])["o"] == "missed_call"


def test_a_callback_conversation_is_stored_with_source_missed_call(hook):
    from services import phone_greeting
    b = hook.rig.env
    cid = asyncio.run(phone_greeting.create_conversation(b, hook.agent, "local-user", "local", source="missed_call"))
    assert b.local_db.get("conversations", cid)["source"] == "missed_call"
    plain = asyncio.run(phone_greeting.create_conversation(b, hook.agent, "local-user", "local"))
    assert b.local_db.get("conversations", plain)["source"] == "phone"


def test_the_calls_page_labels_a_callback_and_treats_it_as_a_phone_call():
    from routers import conversations as conv
    c = {"id": "c1", "agent_id": "a", "source": "missed_call", "created_at": "2026-11-04T10:00:00+00:00"}
    turns = [{"role": "agent", "text_content": "Hello", "created_at": "2026-11-04T10:00:01+00:00"}]
    row = conv.build_call(c, turns, "Reception")
    assert row["called_back"] is True and row["channel"] == "phone"
    assert row["outcome"]["key"] == "ended_early"          # a phone call the person never spoke on
    plain = conv.build_call({**c, "source": "phone"}, turns, "Reception")
    assert plain["called_back"] is False and plain["channel"] == "phone"
