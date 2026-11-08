"""GET /api/ready and /api/usage/*: shapes, no credits spent, the budget and provider-per-lane story."""
import time

import pytest

from core.errors import AppError
from mcp_servers import appointments as ap
from services import gnani_gateway as gw
from services import usage_ledger as ul

LOCAL = {"storage-mode": "local"}


def get(app_client, path):
    r = app_client.get(path, headers=LOCAL)
    assert r.status_code == 200, r.text
    return r.json()


def seed():
    now = time.time()
    ul.LEDGER.append({"t": "req", "kind": "tts", "lane": "live", "chars": 200, "credits": 0.58, "latency_ms": 420,
                      "queue_ms": 5, "cache_hit": False, "ok": True, "conversation_id": "call-1"})
    ul.LEDGER.append({"t": "req", "kind": "stt", "lane": "live", "seconds": 4.0, "credits": 0.0388, "latency_ms": 610,
                      "queue_ms": 0, "cache_hit": False, "ok": True, "conversation_id": "call-1"})
    ul.LEDGER.append({"t": "req", "kind": "tts", "lane": "live", "chars": 90, "credits": 0.0, "ok": False,
                      "degraded": "busy", "action": "hold_line", "message": "Gnani is busy", "conversation_id": "call-2"})
    return now


def test_ready_without_a_key_is_not_ready_and_says_what_is_missing(app_client):
    r = app_client.get("/api/ready")          # public like /health: no sign-in
    assert r.status_code == 200
    body = r.json()
    assert body["gnani_configured"] is False and body["spends_credits"] is False and body["ok"] is False
    assert body["ledger"]["writable"] is True and body["gateway"]["slot"]["busy"] is False
    assert {"stt", "tts", "clone"} == set(body["gateway"]["breakers"]) and body["gateway"]["cache"]["max_bytes"] > 0
    assert body["gateway"]["deadline_s"] == {"live": 4.0, "desk": 60.0, "bench": 600.0}
    assert any("GNANI_API_KEY" in p for p in body["problems"])
    live = body["providers"]["live"]
    assert live["stt"]["name"] == "Gnani Prisma" and live["stt"]["state"] == "needs_key" and live["tts"]["name"] == "Gnani Timbre"
    assert body["hold_lines"]["complete"] is False and body["budget"]["budget"] == 5000


def test_ready_with_a_key_shows_gnani_per_lane_and_never_calls_it(app_client, monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-real"); monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1"); monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setattr("services.gnani_client._request", lambda *a, **k: pytest.fail("ready must not call Gnani"))
    body = get(app_client, "/api/ready")
    assert body["gnani_configured"] is True and body["providers"]["desk"]["tts"] == {
        "provider": "gnani", "name": "Gnani Timbre", "state": "ready", "why": "GNANI_API_KEY is set."}
    assert all("held lines" in p for p in body["problems"]) and len(body["problems"]) == 1     # lines not prepared yet
    assert body["ok"] is True


def test_ready_says_when_the_language_model_is_not_set(app_client, monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "test-key-not-real")
    body = get(app_client, "/api/ready")
    assert body["llm_configured"] is False and body["ok"] is False
    assert any("set LLM_API_KEY, LLM_BASE_URL, LLM_MODEL" in p for p in body["problems"])


def test_ready_reports_an_open_breaker_and_a_held_back_desk_lane(app_client, monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("GNANI_CREDIT_BUDGET", "10")
    ul.record_request(kind="tts", credits=9.0)                       # past the 80% line, inside the reserve
    gw.GATEWAY.breakers["tts"].failure("server_error", trip=True)
    body = get(app_client, "/api/ready")
    assert body["ok"] is False and body["gateway"]["breakers"]["tts"]["state"] == "open"
    tts = body["providers"]["live"]["tts"]
    assert tts["name"] == "Gnani Timbre" and tts["state"] == "unavailable" and "callback" in tts["why"]
    assert body["providers"]["live"]["stt"]["state"] == "ready"
    assert body["providers"]["desk"]["stt"]["state"] == "held_back" and "reserve" in body["providers"]["desk"]["stt"]["why"]
    assert body["budget"]["reserve_reached"] is True


def test_ready_reports_an_unwritable_ledger(app_client, monkeypatch, tmp_path):
    blocker = tmp_path / "f"
    blocker.write_text("x")
    monkeypatch.setenv("VAKYA_USAGE_FILE", str(blocker / "x" / "u.jsonl"))
    body = get(app_client, "/api/ready")
    assert body["ok"] is False and body["ledger"]["writable"] is False
    assert any("ledger" in p for p in body["problems"])


def test_summary_shape_and_numbers(app_client, monkeypatch):
    seed()
    monkeypatch.setattr(ap, "all_bookings", lambda: [
        {"status": "booked", "conversation_id": "call-1", "created_at": "2099-01-01T00:00:00+05:30"},
        {"status": "cancelled", "conversation_id": "call-1", "created_at": "2099-01-01T00:00:00+05:30"},
        {"status": "booked", "conversation_id": None, "created_at": "2099-01-01T00:00:00+05:30"},     # a staff booking
        {"status": "booked", "conversation_id": "old", "created_at": "2001-01-01T00:00:00+05:30"}])
    s = get(app_client, "/api/usage/summary?days=7")
    assert s["window_days"] == 7 and s["totals"]["requests"] == 3 and s["totals"]["credits"] == pytest.approx(0.62, abs=0.01)
    assert s["by_kind"]["tts"]["requests"] == 2 and s["by_kind"]["stt"]["latency_p50_ms"] == 610.0
    assert s["degraded"]["by_reason"] == {"busy": 1} and s["degraded"]["recent"][0]["conversation_id"] == "call-2"
    assert s["degraded"]["recent"][0]["action"] == "hold_line" and s["degraded"]["hold_lines_played"] == 0
    assert s["bookings"] == 1 and s["cost_per_booking"] == pytest.approx(0.62) and s["calls"] == 2
    assert s["rates"]["tts_per_1k_chars"]["source"] == "default" and "nobody has measured" in s["estimate_note"]
    assert len(s["daily"]) == 7 and s["budget"]["budget"] == 5000 and s["providers"]["live"]["stt"]["name"] == "Gnani Prisma"
    assert "gateway" in s


def test_summary_survives_an_unreadable_bookings_store(app_client, monkeypatch):
    seed()

    def boom():
        raise RuntimeError("store locked")
    monkeypatch.setattr(ap, "all_bookings", boom)
    s = get(app_client, "/api/usage/summary")
    assert s["bookings"] is None and s["cost_per_booking"] is None and s["totals"]["requests"] == 3


def test_summary_validates_days(app_client):
    assert app_client.get("/api/usage/summary?days=0", headers=LOCAL).status_code == 422
    assert app_client.get("/api/usage/summary?days=9999", headers=LOCAL).status_code == 422


def test_calls_map_and_one_call(app_client):
    seed()
    m = get(app_client, "/api/usage/calls?days=30")
    assert m["estimated"] is True and m["calls"]["call-1"]["credits"] == pytest.approx(0.6188, abs=0.0001)
    assert m["calls"]["call-2"]["degraded"] == 1 and m["calls"]["call-2"]["degraded_reasons"] == ["busy"]
    one = get(app_client, "/api/usage/calls/call-1")
    assert one["requests"] == 2 and one["by_kind"]["stt"]["seconds"] == 4.0 and one["degraded"] == []
    none = get(app_client, "/api/usage/calls/never-heard-of-it")
    assert none["requests"] == 0 and none["credits"] == 0


@pytest.mark.parametrize("source,lane", [("phone", "live"), ("web", "live"), ("missed_call", "live"), (None, "desk")])
def test_a_turn_starts_a_scope_in_its_lane_and_credits_follow_the_conversation(source, lane):
    """turn_begin opens the Gnani scope (phone and web calls are the live lane); turn_context binds the conversation id."""
    import asyncio
    from services import turn_pipeline

    async def go():
        await turn_pipeline.turn_begin(None, "a1", "u1", storage_mode="local", agent={"id": "a1"}, source=source)
        assert gw.current_scope().lane == lane and gw.current_scope().user_id == "u1"
        await asyncio.to_thread(gw.execute, "tts", lambda: b"a", units={"chars": 10})
        gw.bind_conversation("conv-9")
    asyncio.run(go())
    assert ul.calls_map(ul.LEDGER.records())["conv-9"]["requests"] == 1
    assert ul.LEDGER.records()[0]["lane"] == lane
