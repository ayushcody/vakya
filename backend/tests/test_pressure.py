"""Pressure tests: many writers at once on the same slot and the same files, hostile input on the main HTTP routes, odd
knowledge uploads, and retrieval under load with no embeddings. Everything runs offline (no Gnani, Vobiz, WhatsApp or hosted
model); the whole module takes a few seconds."""
import asyncio
import io
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import pytest

from core import state
from mcp_servers import appointments as ap
from services import booking_audit, booking_notify, caller_profiles, knowledge, schedule_bookings as sb, schedules as sch
from tests import faq_fixture as F
from tests.test_offline_agents import LOCAL, env, make_agent  # noqa: F401  (env is a fixture)

NOW = datetime(2026, 11, 3, 9, 0)        # a Tuesday
THURSDAY, FRIDAY, SATURDAY = date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 7)
ERROR_KEYS = {"error", "code", "details", "detail"}


@pytest.fixture(autouse=True)
def _private_phone_files(monkeypatch, tmp_path):
    """The phone hooks write call and missed-call records: keep them out of backend/data."""
    monkeypatch.setenv("VAKYA_CALL_RECORDS_FILE", str(tmp_path / "phone" / "call_records.jsonl"))
    monkeypatch.setenv("VAKYA_MISSED_CALLS_FILE", str(tmp_path / "phone" / "missed_calls.jsonl"))


def week():
    return {d: [{"start": "10:00", "end": "13:00"}, {"start": "17:00", "end": "20:00"}]
            for d in ("mon", "tue", "wed", "thu", "fri", "sat")} | {"sun": []}


@pytest.fixture()
def clinic(monkeypatch):
    monkeypatch.setattr(sch, "now", lambda tz=None: NOW)
    monkeypatch.setattr(ap, "now", lambda: NOW)
    return sch.create({"name": "Doctor consultations", "slot_minutes": 30, "capacity": 1, "week": week(), "agent_id": "agent-1"})


def run_all(fn, jobs, workers=24):
    """Run fn(job) for every job on `workers` threads released together; returns [("ok", value) | ("err", exception)]."""
    gate = threading.Barrier(min(workers, len(jobs)))
    started = []

    def one(job):
        if len(started) < gate.parties:
            started.append(1)
            try:
                gate.wait(timeout=5)
            except threading.BrokenBarrierError:
                pass
        try:
            return ("ok", fn(job))
        except Exception as e:  # noqa: BLE001
            return ("err", e)
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(one, jobs))


def stored_bookings():
    path = ap.data_path()
    with open(path, encoding="utf-8") as f:
        return json.load(f)["bookings"]          # raises if the file is not whole JSON


def live_slots(rows, schedule_id):
    return [b["start"] for b in rows if b.get("schedule_id") == schedule_id and b.get("status") != "cancelled"]


def audit_lines():
    with open(booking_audit.audit_path(), encoding="utf-8") as f:
        return [json.loads(line) for line in f.read().splitlines()]       # every line whole JSON


def assert_error_shape(r):
    body = r.json()
    assert ERROR_KEYS <= set(body), body
    assert isinstance(body["code"], str) and body["code"]


def assert_clean(r, allow_ok=False):
    """Never a 500; an error answers in the app's shape."""
    assert r.status_code < 500, (r.status_code, r.text[:300])
    if r.status_code >= 400:
        assert_error_shape(r)
    else:
        assert allow_ok, (r.status_code, r.text[:300])


# ---------------------------------------------------------------------------------------------- 1. the same slot at once

def test_forty_threads_booking_one_slot_make_exactly_one_booking(clinic):
    def book(i):
        return sb.create(clinic, day=THURSDAY, at="18:00", name=f"Caller Number{chr(65 + i % 26)}", phone=f"98765{i:05d}",
                         caller=False, source="staff", actor="staff", now=NOW)
    out = run_all(book, range(40))
    wins = [v for kind, v in out if kind == "ok"]
    losses = [v for kind, v in out if kind == "err"]
    assert len(wins) == 1, [type(e).__name__ for e in losses]
    assert all(isinstance(e, sb.BookingRefused) for e in losses)
    rows = stored_bookings()
    assert live_slots(rows, clinic["id"]) == ["2026-11-05T18:00"]
    assert len({b["booking_id"] for b in rows}) == len(rows)
    lines = audit_lines()
    assert [l["event"] for l in lines].count("create") == 1 and [l["event"] for l in lines].count("refusal") == 39


def test_many_threads_on_different_slots_lose_no_write(clinic):
    slots = [(d, f"{h:02d}:{m:02d}") for d in (THURSDAY, FRIDAY, SATURDAY) for h in (10, 11, 12, 17, 18, 19) for m in (0, 30)]
    out = run_all(lambda s: sb.create(clinic, day=s[0], at=s[1], name="Asha Rao", phone="9876543210", caller=False,
                                      source="staff", actor="staff", now=NOW), slots)
    assert [v for kind, v in out if kind == "err"] == []
    rows = stored_bookings()
    assert len(rows) == len(slots) == 36
    assert len({b["booking_id"] for b in rows}) == 36                     # next_booking_id never handed out twice
    assert len(set(live_slots(rows, clinic["id"]))) == 36
    assert [l["event"] for l in audit_lines()].count("create") == 36


def test_moves_and_cancels_racing_for_the_same_slots_keep_every_rule(clinic):
    ids = [sb.create(clinic, day=THURSDAY, at=at, name="Asha Rao", phone="9876543210", caller=False, source="staff",
                     now=NOW)["booking_id"] for at in ("10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "17:00", "17:30")]
    targets = ["10:00", "10:30", "11:00"]                                  # three free Friday slots, many wanting them
    rng = random.Random(7)
    jobs = [("move", bid, rng.choice(targets)) for bid in ids for _ in range(4)] + [("cancel", bid, None) for bid in ids[::3]] * 2

    def op(job):
        kind, bid, at = job
        if kind == "move":
            return sb.reschedule(bid, None, day=FRIDAY, at=at, caller=False, actor="staff", now=NOW)
        return sb.cancel(bid, None, caller=False, actor="staff")
    out = run_all(op, jobs)
    for kind, v in out:
        if kind == "err":
            assert isinstance(v, (sb.BookingRefused, sb.BookingProblem)), repr(v)   # refused or already moved/cancelled, never a crash
    rows = stored_bookings()
    assert len({b["booking_id"] for b in rows}) == len(rows) == 8            # nothing lost, nothing duplicated
    live = live_slots(rows, clinic["id"])
    assert len(live) == len(set(live)), live                                  # capacity 1: never two on one slot
    for b in rows:
        assert b["start"].startswith(b["date"]) and b["start"].endswith(b["time"]) and b["version"] >= 1
    assert all(l["event"] in booking_audit.EVENTS for l in audit_lines())


def test_the_desk_api_never_double_books_under_concurrent_requests(app_client, clinic):
    def post(i):
        return app_client.post("/api/bookings", headers=LOCAL, json={
            "schedule_id": clinic["id"], "start": "2026-11-05T19:00", "name": "Asha Rao", "phone": f"98765{i:05d}"})
    out = run_all(post, range(16), workers=8)
    codes = sorted(v.status_code for _, v in out)
    assert codes == [200] + [409] * 15, codes
    for _, r in out:
        if r.status_code == 409:
            assert r.json()["code"] == "SLOT_UNAVAILABLE"
    assert len(live_slots(stored_bookings(), clinic["id"])) == 1


# ---------------------------------------------------------------------------------------------- 2. the JSON files

def test_caller_profiles_file_survives_concurrent_writers():
    store = caller_profiles.LocalFileStore()

    def put(i):
        store.put({"owner": f"owner{i % 3}", "number": f"98765{i:05d}", "name": "Asha Rao " + "x" * 200, "call_count": i})
        store.get(f"owner{i % 3}", f"98765{i:05d}")
        return i
    out = run_all(put, range(120))
    assert all(kind == "ok" for kind, _ in out)
    with open(store.path, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 120
    assert sum(len(store.list(f"owner{k}")) for k in range(3)) == 120
    assert not [n for n in os.listdir(os.path.dirname(store.path)) if n.endswith(".tmp")]    # no stray temp files


def test_whatsapp_outbox_survives_concurrent_changes():
    def change(i):
        booking = {"booking_id": f"A{i}", "phone": f"98765{i:05d}", "name": "Asha Rao", "date": "2026-11-05", "time": "18:00",
                   "agent_id": "agent-1", "schedule_name": "Clinic"}
        return booking_notify.booking_changed("booked", booking, source="staff")
    out = run_all(change, range(80))
    assert all(kind == "ok" for kind, _ in out)
    path = booking_notify.file_path()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data["items"], dict) and isinstance(data["messages"], list)


def test_whatsapp_consent_and_messages_survive_concurrent_writers(monkeypatch):
    monkeypatch.setenv("WHATSAPP_PREVIEW", "on")
    monkeypatch.setenv("WHATSAPP_SEND_DELAY_S", "3600")
    monkeypatch.setenv("WHATSAPP_BACKSTOP_S", "3600")

    def consent_and_change(i):
        phone = f"98765{i:05d}"
        booking_notify.consent_from_readback(phone)
        booking_notify.booking_changed("booked", {"booking_id": f"B{i}", "phone": phone, "name": "Asha Rao", "date": "2026-11-05",
                                                   "time": "18:00", "agent_id": "agent-1"}, source="staff")
        booking_notify.counts()
        booking_notify.recent_messages()
    out = run_all(consent_and_change, range(60))
    assert all(kind == "ok" for kind, _ in out), [repr(v) for k, v in out if k == "err"][:3]
    with open(booking_notify.file_path(), encoding="utf-8") as f:
        assert isinstance(json.load(f), dict)
    booking_notify.reset()


def test_audit_log_lines_never_interleave():
    def write(i):
        return booking_audit.record("create", actor="staff", booking_id=f"A{i}", schedule_id="S1", phone="9876543210",
                                    reason="x" * (1 + (i * 37) % 3000) + " नमस्ते 😀", after={"date": "2026-11-05", "time": "18:00"})
    out = run_all(write, range(200), workers=32)
    assert all(v is not None for _, v in out)
    lines = audit_lines()
    assert len(lines) == 200 and len({l["booking_id"] for l in lines}) == 200
    assert all(l["phone"] == "••••3210" for l in lines)                 # never a whole number on disk


def test_audit_log_ignores_a_torn_line_and_a_missing_file(tmp_path, monkeypatch):
    assert booking_audit.read(10) == []
    path = tmp_path / "audit" / "booking_audit.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"event":"create","booking_id":"A1"}\n{"event":"cre\n\xff\xfe garbage\n[1,2]\n')
    monkeypatch.setenv("VAKYA_BOOKING_AUDIT_FILE", str(path))
    got = booking_audit.read(10)
    assert [e["booking_id"] for e in got] == ["A1"]


def test_stores_read_back_a_corrupt_file_as_empty_instead_of_crashing(tmp_path, monkeypatch):
    bad = tmp_path / "caller_profiles.json"
    bad.write_bytes(b'{"o|1": {"owner": "o"')                                 # cut off mid-write
    monkeypatch.setenv("VAKYA_CALLER_PROFILES_FILE", str(bad))
    assert caller_profiles.LocalFileStore().list("o") == []
    out = tmp_path / "outbox.json"
    out.write_bytes(b"[1, 2, 3]")                                             # valid JSON of the wrong shape
    monkeypatch.setenv("VAKYA_WHATSAPP_FILE", str(out))
    data = booking_notify._load()
    assert set(data) >= {"consent", "asks", "items", "sent_keys", "messages"}


# ---------------------------------------------------------------------------------------------- 3. hostile input

HUGE = "A" * 200_000
HOSTILE_TEXT = ["", " ", "\x00", "0", "-1", "null", "true", "😀" * 500, "नमस्ते " * 300, "मराठी‍‌", "ａｂｃ", "'; DROP TABLE x; --",
                "<script>alert(1)</script>", "../../etc/passwd", "‮evil", HUGE]


def test_bookings_route_survives_hostile_bodies(app_client, clinic):
    bodies = [None, [], "x", 5, {}, {"schedule_id": clinic["id"]}, {"schedule_id": 7, "start": 1, "name": []},
              {"schedule_id": clinic["id"], "start": "2026-11-05T18:00", "name": "A"},
              {"schedule_id": clinic["id"], "start": "tomorrow", "name": "Asha Rao"},
              {"schedule_id": clinic["id"], "start": "2026-13-45T99:99", "name": "Asha Rao"},
              {"schedule_id": clinic["id"], "start": "2026-11-05T18:00", "name": "Asha Rao", "phone": "12"},
              {"schedule_id": clinic["id"], "start": "2026-11-05T18:00", "name": "Asha Rao", "party": "many"},
              {"schedule_id": clinic["id"], "start": "2026-11-05T18:00", "name": "Asha Rao", "nights": 10 ** 12},
              {"schedule_id": "nope", "start": "2026-11-05T18:00", "name": "Asha Rao"},
              {"schedule_id": clinic["id"], "start": "2026-11-05T18:00", "name": "Asha Rao", "note": HUGE, "phone": HUGE}]
    for text in HOSTILE_TEXT:
        bodies.append({"schedule_id": clinic["id"], "start": "2026-11-06T10:00", "name": text, "note": text, "phone": text})
        bodies.append({"schedule_id": text, "start": text, "name": "Asha Rao"})
    for body in bodies:
        r = app_client.post("/api/bookings", headers=LOCAL, json=body)
        assert_clean(r, allow_ok=True)           # a stay length on a slot schedule is simply ignored: a 200 is right
    r = app_client.post("/api/bookings", headers={**LOCAL, "content-type": "application/json"}, content=b"{not json")
    assert_clean(r)
    r = app_client.post("/api/bookings", headers={**LOCAL, "content-type": "application/json"}, content=b"\xff\xfe\x00")
    assert_clean(r)
    if os.path.exists(ap.data_path()):
        stored_bookings()                                                  # the store is still whole JSON


def test_devanagari_and_emoji_names_are_accepted_or_refused_cleanly(app_client, clinic):
    ok = app_client.post("/api/bookings", headers=LOCAL, json={"schedule_id": clinic["id"], "start": "2026-11-06T10:00",
                                                                "name": "आशा देशमुख", "phone": "98765 43210", "note": "बुखार 🤒"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["booking"]["name"] == "आशा देशमुख"
    bad = app_client.post("/api/bookings", headers=LOCAL, json={"schedule_id": clinic["id"], "start": "2026-11-06T10:30",
                                                                "name": "😀😀😀", "phone": "9876543210"})
    assert bad.status_code == 422 and bad.json()["code"] == "INVALID_NAME"


@pytest.mark.parametrize("booking_id", ["", " ", "A", "A" * 5000, "😀", "नमस्ते", "../../x", "%00", "A10; DROP", "a10", "‮"])
def test_booking_ids_that_do_not_exist_answer_404_or_4xx(app_client, clinic, booking_id):
    for r in (app_client.delete(f"/api/bookings/{booking_id}", headers=LOCAL),
              app_client.patch(f"/api/bookings/{booking_id}", headers=LOCAL, json={"start": "2026-11-06T10:00"}),
              app_client.get(f"/api/bookings/{booking_id}/evidence", headers=LOCAL)):
        assert r.status_code < 500, (booking_id[:20], r.status_code, r.text[:200])
        if r.status_code >= 400 and r.headers.get("content-type", "").startswith("application/json"):
            assert_error_shape(r)


@pytest.mark.parametrize("query", ["?from=" + "9" * 5000, "?to=%00", "?limit=-1", "?agent_id=" + "x" * 10_000, "?from=2026-11-05&from=bad",
                                   "?from=２０２６-११-०५", "?from=😀"])
def test_booking_list_query_garbage(app_client, query):
    r = app_client.get("/api/bookings" + query, headers=LOCAL)
    assert r.status_code < 500
    if r.status_code >= 400:
        assert_error_shape(r)


def test_moving_a_booking_to_hostile_times(app_client, clinic):
    row = sb.create(clinic, day=THURSDAY, at="18:00", name="Asha Rao", phone="9876543210", caller=False, source="staff", now=NOW)
    for start in ["", "x", "2026-11-05T25:00", "0000-00-00", "9999-12-31T23:59", "1900-01-01T10:00", "2026-11-05T18:00",
                  "2026-11-08T10:00", "😀", HUGE]:
        r = app_client.patch(f"/api/bookings/{row['booking_id']}", headers=LOCAL, json={"start": start})
        assert_clean(r, allow_ok=True)
    for body in (None, {}, {"start": 5}, {"start": "2026-11-06T10:00", "nights": "x"}, {"start": "2026-11-06T10:00", "nights": -3}):
        assert_clean(app_client.patch(f"/api/bookings/{row['booking_id']}", headers=LOCAL, json=body), allow_ok=True)


def test_agents_route_survives_hostile_bodies(app_client, env):
    bodies = [None, [], "x", 3, {}, {"name": None}, {"name": 5, "role_description": []}, {"name": "Ria", "role_description": HUGE},
              {"name": HUGE, "role_description": "refund helper"}, {"name": "", "role_description": ""},
              {"name": "Ria", "role_description": "x", "supported_languages": "hi"},
              {"name": "Ria", "role_description": "x", "supported_languages": ["xx"] * 500},
              {"name": "Ria", "role_description": "x", "tools": "all"}, {"name": "Ria", "role_description": "x", "template_id": "../../x"}]
    for text in HOSTILE_TEXT:
        bodies.append({"name": text, "role_description": text})
    for body in bodies:
        assert_clean(app_client.post("/api/agents", headers=LOCAL, json=body), allow_ok=True)
    a = make_agent(app_client)
    for body in (None, [], {"greeting": 5}, {"supported_languages": "hi"}, {"greeting": HUGE}, {"name": "😀" * 400},
                 {"voice_id": {"x": 1}}, {"tools": [1, 2]}):
        assert_clean(app_client.put(f"/api/agents/{a['id']}", headers=LOCAL, json=body), allow_ok=True)
    for agent_id in ("", "😀", "A" * 5000, "../x", "%00", "नमस्ते"):
        for r in (app_client.get(f"/api/agents/{agent_id}", headers=LOCAL), app_client.delete(f"/api/agents/{agent_id}", headers=LOCAL),
                  app_client.get(f"/api/agents/{agent_id}/documents", headers=LOCAL)):
            assert r.status_code < 500, (agent_id[:10], r.status_code)


def test_bad_and_missing_credentials_never_500(app_client, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "x" * 40)
    monkeypatch.delenv("VAKYA_ALLOW_LOCAL_MODE", raising=False)
    monkeypatch.delenv("VAKYA_ALLOW_DEMO_TOKENS", raising=False)
    tokens = ["", "Bearer", "Bearer ", "Bearer x", "Bearer " + "A" * 50_000, "Bearer a.b.c", "Basic Zm9vOmJhcg==", "Bearer 😀",
              "Bearer eyJhbGciOiJub25lIn0.eyJzdWIiOiJ4In0.", "Bearer eyJhbGciOiJIUzI1NiJ9.e30.AAAA", "bearer\x00x"]
    for tok in tokens:
        try:
            headers = {"Authorization": tok} if tok else {}
            headers.update({"storage-mode": "local"})
            for method, path in (("get", "/api/bookings"), ("get", "/api/agents"), ("post", "/api/bookings"),
                                 ("delete", "/api/bookings/A10"), ("get", "/api/phone/status"), ("get", "/api/phone/calls")):
                r = getattr(app_client, method)(path, headers=headers)
                assert r.status_code in (401, 403, 422), (tok[:20], path, r.status_code)
                assert_error_shape(r)
        except UnicodeEncodeError:
            continue        # a header the HTTP client itself refuses to send


def test_an_expired_or_foreign_token_is_refused_on_the_phone_routes(monkeypatch):
    import phone_public
    from fastapi.testclient import TestClient
    from routers import phone as px
    monkeypatch.setenv("VAKYA_SESSION_SECRET", "pressure-secret")
    client = TestClient(phone_public.public_app, raise_server_exceptions=False)
    expired = px.make_token("a1", "u1", "local", ttl=-60, kind="ans")
    stream_token = px.make_token("a1", "u1", "local", ttl=600)          # a stream token is not an answer token
    tampered = px.make_token("a1", "u1", "local", ttl=600, kind="ans")[:-3] + "AAA"
    for token in (expired, tampered, "", "x", "." * 5, "a." * 400, "😀", "%00", "A" * 20_000):
        a = client.post(px.ANSWER_PATH + "/" + token, data={"CallUUID": "c1", "From": "+919876543210", "Direction": "inbound"})
        assert a.status_code in (404, 405, 414), (token[:12], a.status_code)
        s = client.post(px.STATUS_CALLBACK_PATH + "/" + token, data={"CallUUID": "c1", "CallStatus": "completed"})
        assert s.status_code in (200, 404) and (s.status_code == 404 or s.json() == {"ok": False})
        m = client.post(px.MISSED_CALL_PATH + "/" + token, data={"From": "+919876543210"})
        assert m.status_code in (200, 404) and (m.status_code == 404 or "Hangup" in m.text)
    assert client.post(px.ANSWER_PATH + "/" + stream_token).status_code == 404


def test_phone_webhooks_survive_hostile_bodies(monkeypatch):
    import phone_public
    from fastapi.testclient import TestClient
    from routers import phone as px
    monkeypatch.setenv("VAKYA_SESSION_SECRET", "pressure-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")
    client = TestClient(phone_public.public_app, raise_server_exceptions=False)
    ans = px.make_token("a1", "u1", "local", ttl=600, kind="ans")
    missed = px.make_token("a1", "u1", "local", ttl=600, kind="missed")
    bodies = [b"", b"\x00\xff" * 10, b"{", b"[]", b'"x"', b"null", b"A" * 3_000_000, "CallUUID=" + "x" * 100_000, "From=%F0%9F%98%80&Direction=inbound",
              "From=" + "9" * 5000, "CallUUID=नमस्ते&From=नमस्ते&To=&Direction=", json.dumps({"CallUUID": 5, "From": None, "Direction": []})]
    for body in bodies:
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        for ctype in ("application/x-www-form-urlencoded", "application/json", "text/plain", "multipart/form-data; boundary=x"):
            for path in (px.ANSWER_PATH + "/" + ans, px.STATUS_CALLBACK_PATH + "/" + ans, px.STATUS_CALLBACK_PATH + "/" + missed,
                         px.MISSED_CALL_PATH + "/" + missed, px.MISSED_CALL_PATH + "/" + ans):
                r = client.post(path, content=data, headers={"content-type": ctype})
                assert r.status_code < 500, (path[:40], ctype, r.status_code, r.text[:200])
    for path in (px.ANSWER_PATH + "/" + ans, px.MISSED_CALL_PATH + "/" + missed):
        assert client.get(path + "?From=" + "%F0%9F%98%80" * 200 + "&CallUUID=" + "x" * 50_000).status_code < 500


def test_the_call_stream_refuses_garbage_without_a_crash(monkeypatch):
    import phone_public
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from routers import phone as px
    monkeypatch.setenv("VAKYA_SESSION_SECRET", "pressure-secret")
    client = TestClient(phone_public.public_app, raise_server_exceptions=False)
    ans = px.make_token("a1", "u1", "local", ttl=600, kind="ans")
    expired = px.make_token("a1", "u1", "local", ttl=-5)
    for token in ("", "x", "A" * 5000, expired, ans, "😀", "..."):
        try:
            with client.websocket_connect(px.STREAM_PATH + "/" + token):
                pass
        except WebSocketDisconnect as e:
            assert e.code in (4401, 4404, 1000, 1008)
        except Exception as e:  # noqa: BLE001 - the HTTP layer may refuse the path itself
            assert "404" in repr(e) or "403" in repr(e) or "Not Found" in repr(e), repr(e)


def test_the_place_a_call_route_refuses_bad_bodies_before_any_dialling(app_client, monkeypatch):
    from routers import phone as px

    async def refuse(*a, **k):
        raise AssertionError("a pressure test must never dial")
    monkeypatch.setattr(px, "dial", refuse)
    for body in (None, [], {}, {"agent_id": ""}, {"agent_id": "a", "to": 5}, {"agent_id": "a", "to": "😀", "caller_agreed": True},
                 {"agent_id": "a" * 500, "to": "+919876543210"}, {"agent_id": "a", "to": "9" * 5000, "caller_agreed": True},
                 {"agent_id": "a", "to": "+919876543210", "caller_agreed": "maybe"}, {"agent_id": "a", "to": "+919876543210"}):
        r = app_client.post("/api/phone/call", headers=LOCAL, json=body)
        assert r.status_code in (400, 403, 404, 422, 503), (body, r.status_code, r.text[:200])
        assert_error_shape(r)


# ---------------------------------------------------------------------------------------------- 4. odd knowledge files

def upload(client, agent_id, name, data, ctype="application/octet-stream"):
    return client.post("/api/knowledge/upload", headers=LOCAL, data={"agent_id": agent_id}, files={"file": (name, data, ctype)})


def blank_pdf(pages=1):
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_odd_knowledge_files_answer_4xx_never_500(app_client, env):
    a = make_agent(app_client)
    cases = {
        "empty.txt": b"", "spaces.md": b"   \n\n\t ", "nul.txt": b"\x00\x00\x00",
        "garbage.txt": bytes(range(128, 256)) * 50, "latin1.txt": "café résumé".encode("latin-1"),
        "empty.pdf": b"", "fake.pdf": b"%PDF-1.4 not really", "blank.pdf": blank_pdf(), "html.txt": b"<html><script>x</script></html>",
        "too_big.txt": b"a " * (1024 * 1024 + 8),                       # 2 MB and a bit
        "noext": b"hello", "photo.png": b"\x89PNG\r\n", "double.txt.exe": b"hello world", ".txt": b"hello there friend",
        "emoji😀.txt": "😀 नमस्ते hello world, this is a sentence about refunds.".encode(), "a" * 400 + ".txt": b"refund policy is thirty days",
        "../../etc/passwd.txt": b"refund policy is thirty days",
    }
    for name, data in cases.items():
        r = upload(app_client, a["id"], name, data)
        assert r.status_code < 500, (name[:30], r.status_code, r.text[:200])
        if r.status_code >= 400:
            assert_error_shape(r)
    assert upload(app_client, a["id"], "empty.txt", b"").status_code == 400
    assert upload(app_client, a["id"], "garbage.txt", bytes(range(128, 256)) * 50).status_code == 415
    assert upload(app_client, a["id"], "too_big.txt", b"a " * (1024 * 1024 + 8)).status_code == 413
    assert upload(app_client, a["id"], "blank.pdf", blank_pdf()).status_code == 400
    assert upload(app_client, a["id"], "photo.png", b"\x89PNG").status_code == 415
    names = [d["filename"] for d in app_client.get(f"/api/agents/{a['id']}/documents", headers=LOCAL).json()["documents"]]
    assert all("/" not in n for n in names)                               # a path in the file name is cut to its last part


def test_upload_field_garbage(app_client, env):
    a = make_agent(app_client)
    for data, files in (({}, None), ({"agent_id": a["id"]}, None), ({"agent_id": ""}, {"file": ("a.txt", b"hello world", "text/plain")}),
                        ({"agent_id": "😀" * 400}, {"file": ("a.txt", b"hello world", "text/plain")}),
                        ({"agent_id": "nope"}, {"file": ("a.txt", b"hello world", "text/plain")})):
        r = app_client.post("/api/knowledge/upload", headers=LOCAL, data=data, files=files)
        assert r.status_code in (404, 400, 422), (data, r.status_code)
        assert_error_shape(r)


def test_a_two_megabyte_text_file_and_a_devanagari_only_file_are_ingested_and_found(app_client, env):
    a = make_agent(app_client)
    started = time.monotonic()
    big = ("The refund window is thirty days from purchase. Parking is free behind the building.\n" * 22000).encode()   # 1.9 MB
    assert len(big) < 2 * 1024 * 1024
    r = upload(app_client, a["id"], "big.txt", big, "text/plain")
    assert r.status_code == 200, r.text
    assert time.monotonic() - started < 15
    deva = "# अक्सर पूछे जाने वाले सवाल\nप्रश्न: क्लिनिक कब खुलता है?\nउत्तर: क्लिनिक सुबह दस बजे से दोपहर एक बजे तक खुलता है।\n"
    r = upload(app_client, a["id"], "hindi.txt", deva.encode("utf-8"), "text/plain")
    assert r.status_code == 200 and r.json()["chunks_created"] >= 1
    out = asyncio.run(knowledge.retrieve_context(a["id"], "क्लिनिक कब खुलता है", user_id="local-user", storage_mode="local"))
    assert out and any("सुबह दस बजे" in p for p in out)


def test_chunking_odd_text_never_raises():
    from services import chunking
    for text in ["", "\n" * 1000, "?" * 5000, "Q:" * 2000, "Q: \nA: \n" * 300, "# " * 3000, "|" * 4000, "क" * 20000,
                 "😀 " * 10000, "Q: a?\n" + "A: " + "x" * 100_000, "a\x00b\x00" * 1000, ("हिन्दी " * 50 + "।\n") * 400]:
        passages = knowledge._document_passages("odd.txt", text)
        assert isinstance(passages, list)
        assert all(isinstance(p["content"], str) for p in passages)


# ---------------------------------------------------------------------------------------------- 5. retrieval under load

def test_two_hundred_concurrent_queries_with_embeddings_unavailable(monkeypatch, tmp_path):
    from services import retrieval
    from services.local_store import LocalStore
    monkeypatch.setattr(state, "local_db", LocalStore(str(tmp_path / "kb")))
    monkeypatch.setenv("QUESTION_INDEX", "off")
    assert not retrieval.embeddings_configured()                          # no key: BM25 alone
    agent = state.local_db.insert("agents", {"user_id": "local-user", "name": "Ria"})
    asyncio.run(knowledge.ingest_document(agent["id"], "local-user", "local", "faq.txt", F.faq_text()))
    queries = [q for q, _ in F.QUERIES] + ["", " ", "?", "😀", "नमस्ते " * 200, "x" * 20_000, "\x00", "Q: A:", "refund"] * 3

    async def go():
        sigs = [{} for _ in range(200)]
        jobs = [knowledge.retrieve_context(agent["id"], queries[i % len(queries)], user_id="local-user", storage_mode="local",
                                           signals=sigs[i]) for i in range(200)]
        return await asyncio.gather(*jobs), sigs
    started = time.monotonic()
    results, sigs = asyncio.run(go())
    assert time.monotonic() - started < 10
    assert len(results) == 200 and all(isinstance(r, list) for r in results)
    assert [s for s in sigs if s.get("search_mode") != "keyword" or s.get("error")] == []
    asked = {q: k for q, k in F.QUERIES}
    for i, passages in enumerate(results):
        q = queries[i % len(queries)]
        if q in asked:
            assert passages and F.key_of_passage(passages[0]) in F.accepted_keys(asked[q]), (q, passages[:1])


def test_retrieval_for_a_foreign_or_missing_agent_is_a_clean_404(monkeypatch, tmp_path):
    from core.errors import AppError
    from services.local_store import LocalStore
    monkeypatch.setattr(state, "local_db", LocalStore(str(tmp_path / "kb")))
    for agent_id in ("nope", "", "😀", "A" * 5000):
        with pytest.raises(AppError) as info:
            asyncio.run(knowledge.retrieve_context(agent_id, "hello", user_id="local-user", storage_mode="local"))
        assert info.value.status == 404


def test_calls_from_one_number_ending_together_lose_no_count():
    def finish(i):
        return caller_profiles.record_call("owner1", "9876543210", "local", {"language": "hi"}, conversation_id=f"conv-{i}",
                                           returning=i > 0)
    out = run_all(finish, range(40))
    assert all(kind == "ok" and v is not None for kind, v in out), [repr(v) for k, v in out if k != "ok" or v is None][:2]
    [profile] = caller_profiles.LocalFileStore().list("owner1")
    assert profile["call_count"] == 40, profile["call_count"]


def test_schedules_and_blocks_made_at_once_keep_unique_ids_and_whole_files(monkeypatch):
    monkeypatch.setattr(sch, "now", lambda tz=None: NOW)
    out = run_all(lambda i: sch.create({"name": f"Desk {i}", "slot_minutes": 30, "week": week(), "agent_id": f"agent-{i}"}), range(30))
    assert all(kind == "ok" for kind, _ in out), [repr(v) for k, v in out if k == "err"][:2]
    ids = [v["id"] for _, v in out]
    assert len(set(ids)) == 30 and sorted(s["id"] for s in sch.list_schedules()) == sorted(ids)
    first = ids[0]
    blocks = run_all(lambda i: sch.add_block(first, {"start": f"2026-11-{10 + i // 10}T{8 + i % 10:02d}:00",
                                                     "end": f"2026-11-{10 + i // 10}T{8 + i % 10:02d}:30", "reason": "x" * 50}), range(40))
    assert all(kind == "ok" for kind, _ in blocks), [repr(v) for k, v in blocks if k == "err"][:2]
    assert len(sch.list_blocks(first)) == 40 and len({b["id"] for b in sch.list_blocks(first)}) == 40
    with open(os.environ["VAKYA_SCHEDULES_FILE"], encoding="utf-8") as f:
        json.load(f)


def test_the_hearing_store_takes_exactly_its_limit_under_concurrent_corrections():
    from services import hearing
    out = run_all(lambda i: hearing.add_correction("agent-1", f"dactar sahab {i}", f"doctor number {i}"), range(25))
    ok = [v for kind, v in out if kind == "ok"]
    refused = [v for kind, v in out if kind == "err"]
    assert len(ok) == 10 and len(refused) == 15 and all(isinstance(e, hearing.HearingError) for e in refused)
    assert len(hearing.list_rules("agent-1")) == 10
    with open(hearing.data_path(), encoding="utf-8") as f:
        json.load(f)


# ---------------------------------------------------------------------------------------------- 6. every route, garbage in

SKIP_PREFIXES = ("/docs", "/redoc", "/openapi", "/api/phone/call")      # the dial route is covered above and must never dial


def _fill(path, value):
    import re
    return re.sub(r"\{[^}]+\}", value, path)


def test_no_route_answers_500_to_garbage(app_client, env, clinic, monkeypatch):
    routes = []
    for path, ops in app_client.app.openapi()["paths"].items():
        if not path.startswith(SKIP_PREFIXES):
            routes += [(m.upper(), path) for m in ops if m in ("get", "post", "put", "patch", "delete")]
    assert len(routes) > 60, len(routes)
    agent = make_agent(app_client)
    sb.create(clinic, day=THURSDAY, at="18:00", name="Asha Rao", phone="9876543210", caller=False, source="staff", now=NOW)
    bad = []
    payloads = [None, [], {}, "x", {"a": "b"}, {"name": 5, "text": [], "start": {}, "agent_id": None, "id": 1.5}, {"text": "😀" * 3000},
                {"text": "नमस्ते " * 2000, "agent_id": "../x"}]
    for method, path in sorted(routes):
        for value in ("x", "😀", "A" * 3000, "%00", "../..", agent["id"], "A10", clinic["id"]):
            url = _fill(path, value.replace("/", "_") if "/" in value else value)
            for body in payloads if value in ("x", agent["id"], "A10", clinic["id"]) else payloads[:4]:
                kw = {"headers": LOCAL}
                if method in ("POST", "PUT", "PATCH", "DELETE") and body is not None:
                    kw["json"] = body
                try:
                    r = app_client.request(method, url, **kw)
                except Exception as e:  # noqa: BLE001
                    bad.append((method, path, repr(e)[:120]))
                    continue
                if r.status_code >= 500 and r.status_code != 503:
                    bad.append((method, path, r.status_code, r.text[:100]))
    assert bad == [], bad[:20]
