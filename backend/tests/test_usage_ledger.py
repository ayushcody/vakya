"""services/usage_ledger.py: rates, the append-only ledger, the budget guard and the summaries. Time and files are faked."""
import json
import time

import pytest

from services import usage_ledger as ul

NOW = 1_800_000_000.0     # a fixed "now"


def req(**kw):
    base = {"t": "req", "ts": NOW - 60, "kind": "tts", "lane": "live", "credits": 1.0, "latency_ms": 100.0, "queue_ms": 0.0,
            "cache_hit": False, "ok": True, "scope": "s1"}
    base.update(kw)
    return base


# ------------------------------------------------------------------------------------------------------ rates

def test_default_rates_are_marked_unverified_and_env_values_are_marked_measured(monkeypatch):
    r = ul.rates()
    assert r["stt_rest_per_s"]["value"] == 0.0097 and r["stt_stream_per_s"]["value"] == 0.0073
    assert r["tts_per_1k_chars"]["value"] == 2.9 and r["stt_rest_per_s"]["source"] == "default"
    assert not ul.rates_verified()
    monkeypatch.setenv("GNANI_RATE_STT_REST_PER_S", "0.02")
    monkeypatch.setenv("GNANI_RATE_STT_STREAM_PER_S", "0.01")
    monkeypatch.setenv("GNANI_RATE_TTS_PER_1K_CHARS", "3.5")
    assert ul.rates()["stt_rest_per_s"] == {"value": 0.02, "source": "env", "env": "GNANI_RATE_STT_REST_PER_S"}
    assert ul.rates_verified()


def test_credit_estimates_follow_the_units(monkeypatch):
    assert ul.estimate_credits("stt", seconds=10) == pytest.approx(0.097)
    assert ul.estimate_credits("stt_stream", seconds=10) == pytest.approx(0.073)
    assert ul.estimate_credits("tts", chars=2000) == pytest.approx(5.8)
    assert ul.estimate_credits("clone") == 0.0 and ul.estimate_credits("other", seconds=5) == 0.0
    monkeypatch.setenv("GNANI_RATE_TTS_PER_1K_CHARS", "nonsense")
    assert ul.estimate_credits("tts", chars=1000) == pytest.approx(2.9)       # a bad value is ignored, never a crash


# ----------------------------------------------------------------------------------------- ledger and budget

def test_the_ledger_appends_json_lines_and_tracks_the_total(tmp_path):
    ul.record_request(kind="tts", lane="live", credits=2.5, ok=True)
    ul.record_request(kind="stt", lane="desk", credits=0.5, ok=True, conversation_id=None)
    ul.record_bind("s9", "conv-1")
    file_lines = (tmp_path / "usage.jsonl").read_text().splitlines()
    assert len(file_lines) == 3 and all(json.loads(l) for l in file_lines)
    assert "conversation_id" not in json.loads(file_lines[1])                  # None fields are not written
    assert ul.LEDGER.spent() == pytest.approx(3.0)
    ul.LEDGER.reset_memory()                                                   # a restart reads the total back from the file
    assert ul.LEDGER.spent() == pytest.approx(3.0)


def test_a_half_written_line_is_skipped_not_fatal(tmp_path):
    ul.record_request(kind="tts", credits=1.0)
    with open(tmp_path / "usage.jsonl", "a") as f:
        f.write('{"t":"req","kind":"tts","cred')
    ul.LEDGER.reset_memory()
    assert ul.LEDGER.spent() == 1.0 and len(ul.LEDGER.records()) == 1


def test_an_unwritable_ledger_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("VAKYA_USAGE_FILE", str(blocker / "sub" / "usage.jsonl"))   # a file where a folder must be
    assert ul.record_request(kind="tts", credits=1.0) is False
    assert "Error" in ul.LEDGER.last_error
    ok, why = ul.LEDGER.check_writable()
    assert ok is False and why


def test_budget_defaults_and_reserve_maths(monkeypatch):
    st = ul.budget_status(spent=0)
    assert st["budget"] == 5000 and st["reserve_pct"] == 20 and st["reserve"] == 1000 and st["usable_for_desk"] == 4000
    monkeypatch.setenv("GNANI_CREDIT_BUDGET", "200")
    monkeypatch.setenv("GNANI_CREDIT_RESERVE_PCT", "25")
    st = ul.budget_status(spent=149)
    assert (st["reserve"], st["usable_for_desk"], st["left"], st["left_for_desk"]) == (50, 150, 51, 1)
    assert st["reserve_reached"] is False and st["exhausted"] is False and st["spent_rupees"] is None
    assert ul.budget_status(spent=150)["reserve_reached"] is True and ul.budget_status(spent=200)["exhausted"] is True


def test_the_guard_refuses_desk_and_bench_at_the_reserve_and_never_a_live_call(monkeypatch):
    monkeypatch.setenv("GNANI_CREDIT_BUDGET", "100")
    ul.record_request(kind="tts", credits=70.0)
    assert ul.guard("desk", 10.0) is None                      # 80 of 80 usable: exactly at the line is allowed
    msg = ul.guard("desk", 10.5)
    assert msg and "reserve" in msg and "70 of 100" in msg and "20 reserved" in msg
    assert ul.guard("bench", 10.5) and ul.guard("live", 10_000) is None
    monkeypatch.setenv("GNANI_CREDIT_RESERVE_PCT", "0")
    assert ul.guard("desk", 10.5) is None


def test_rupees_only_when_a_rate_is_set(monkeypatch):
    assert ul.rupees(10) is None
    monkeypatch.setenv("GNANI_RUPEES_PER_CREDIT", "0.4")
    assert ul.rupees(10) == 4.0 and ul.budget_status(spent=50)["spent_rupees"] == 20.0


def test_percentiles():
    assert ul.percentile([], 50) is None and ul.percentile([5], 95) == 5
    assert ul.percentile([1, 2, 3, 4], 50) == 2.5 and ul.percentile(list(range(1, 101)), 95) == 95.0


# ---------------------------------------------------------------------------------------------- summaries

def sample():
    return [
        req(kind="tts", credits=2.0, latency_ms=300, queue_ms=10, scope="a", conversation_id="c1"),
        req(kind="tts", credits=0.0, latency_ms=0, queue_ms=0, cache_hit=True, scope="a", conversation_id="c1"),
        req(kind="stt", credits=0.5, latency_ms=500, queue_ms=30, scope="b"),
        {"t": "bind", "ts": NOW - 50, "scope": "b", "conversation_id": "c1"},
        req(kind="tts", credits=0.0, ok=False, degraded="breaker_open", message="paused", scope="c", conversation_id="c2"),
        req(kind="tts", credits=9.0, lane="bench", scope="d"),
        req(kind="tts", credits=100.0, ts=NOW - 40 * 86400, scope="old"),
        req(kind="tts", credits=1.0, user="someone-else", scope="e", conversation_id="c3"),
    ]


def test_summary_counts_credits_latency_cache_degraded_and_costs():
    s = ul.summarize(sample(), days=7, bookings=2, user="me", now=NOW)
    assert s["window_days"] == 7 and s["totals"]["requests"] == 5                  # the 40-day-old and the other user's: out
    assert s["by_kind"]["tts"]["requests"] == 4 and s["by_kind"]["stt"]["requests"] == 1
    assert s["by_kind"]["tts"]["credits"] == 11.0 and s["totals"]["credits"] == 11.5
    assert s["by_kind"]["tts"]["latency_p50_ms"] == 200.0                          # 300 and the bench 100; cache hits and failures are not latency
    assert s["by_kind"]["stt"]["queue_p95_ms"] == 30.0 and s["queue_wait"]["p50_ms"] is not None
    assert s["totals"]["cache_hits"] == 1 and s["totals"]["cache_hit_rate"] == 0.2
    assert s["degraded"]["count"] == 1 and s["degraded"]["by_reason"] == {"breaker_open": 1}
    assert s["degraded"]["recent"][0]["conversation_id"] == "c2"
    assert s["calls"] == 2                                                         # c1 (the bind joined the stt line) and c2
    assert s["cost_per_call"] == 1.25 and s["bookings"] == 2 and s["cost_per_booking"] == 1.25   # bench credits are left out
    assert s["by_lane"]["bench"] == {"requests": 1, "credits": 9.0}
    assert s["rates_verified"] is False and "nobody has measured" in s["estimate_note"]


def test_summary_without_bookings_or_calls_says_none_not_zero():
    s = ul.summarize([req(scope="x")], days=7, bookings=0, now=NOW)
    assert s["cost_per_booking"] is None and s["cost_per_call"] is None and s["calls"] == 0
    assert ul.summarize([], days=7, now=NOW)["by_kind"] == {} and ul.summarize([], days=1, now=NOW)["totals"]["credits"] == 0


def test_rupee_figures_follow_the_rate(monkeypatch):
    monkeypatch.setenv("GNANI_RUPEES_PER_CREDIT", "0.5")
    s = ul.summarize(sample(), days=7, bookings=1, user="me", now=NOW)
    assert s["totals"]["rupees"] == 5.75 and s["cost_per_booking_rupees"] == 1.25 and s["cost_per_call_rupees"] == 0.62


def test_daily_series_has_one_entry_per_day_oldest_first():
    s = ul.summarize(sample(), days=3, now=NOW)
    assert len(s["daily"]) == 3 and s["daily"][0]["date"] < s["daily"][-1]["date"]
    assert sum(d["credits"] for d in s["daily"]) == pytest.approx(s["totals"]["credits"])


def test_per_call_views():
    m = ul.calls_map(sample(), days=30, user="me", now=NOW)
    assert set(m) == {"c1", "c2"} and m["c1"]["credits"] == 2.5 and m["c1"]["requests"] == 3
    assert m["c2"]["degraded"] == 1 and m["c2"]["degraded_reasons"] == ["breaker_open"]
    one = ul.call_usage(sample(), "c1", user="me")
    assert one["credits"] == 2.5 and one["requests"] == 3 and one["estimated"] and one["by_kind"]["stt"]["requests"] == 1
    assert ul.call_usage(sample(), "c3", user="me")["requests"] == 0               # another user's call is not visible
    assert ul.call_usage(sample(), "nope")["items"] == []
