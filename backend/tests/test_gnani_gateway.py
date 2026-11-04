"""services/gnani_gateway.py: one request at a time, lane priority, backoff, the circuit breaker, the cache, the WAV header
fix, scopes and the realtime hold. Nothing here reaches api.vachana.ai (a fake function stands in for the HTTP call)."""
import asyncio
import json
import struct
import threading
import time

import httpx
import pytest

from core.errors import AppError
from services import gnani_client as gc
from services import gnani_gateway as gw
from services import usage_ledger as ul

G = gw.GATEWAY


def lines(kind="req"):
    return [r for r in ul.LEDGER.records() if r.get("t") == kind]


def run(fn=lambda: "ok", **kw):
    kw.setdefault("units", {"chars": 100})
    return G.execute("tts", fn, **kw)


def err(code, status, http=None, **details):
    return AppError(code, "x", http or status, {"provider": "gnani", "status": status, **details})


# ------------------------------------------------------------------------------------------------ one at a time

def test_two_requests_never_overlap():
    active, peak, lock = [0], [0], threading.Lock()

    def fn():
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.03)
        with lock:
            active[0] -= 1
        return "ok"

    threads = [threading.Thread(target=run, args=(fn,), kwargs={"lane": "desk"}) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert peak[0] == 1 and len(lines()) == 6
    assert any(r["queue_ms"] > 0 for r in lines())      # the later ones waited, and the wait was measured


def test_lanes_are_served_live_then_desk_then_bench():
    gate, order = threading.Event(), []
    first = threading.Thread(target=run, args=(lambda: gate.wait(2),), kwargs={"lane": "bench"})
    first.start()
    while not G.slot.state()["busy"]:
        time.sleep(0.005)
    threads = []
    for lane in ("bench", "desk", "live", "desk"):
        t = threading.Thread(target=run, args=(lambda lane=lane: order.append(lane) or "ok",), kwargs={"lane": lane})
        t.start()
        threads.append(t)
        while G.slot.state()["waiting"] < len(threads):    # queued in this order
            time.sleep(0.005)
    gate.set()
    [t.join() for t in threads + [first]]
    assert order == ["live", "desk", "desk", "bench"]


def test_a_waiter_that_runs_out_of_time_is_told_to_hold_the_line(monkeypatch):
    monkeypatch.setenv("GNANI_QUEUE_WAIT_LIVE_S", "0.05")
    gate = threading.Event()
    holder = threading.Thread(target=run, args=(lambda: gate.wait(2),), kwargs={"lane": "desk"})
    holder.start()
    while not G.slot.state()["busy"]:
        time.sleep(0.005)
    with pytest.raises(AppError) as e:
        run(lane="live")
    gate.set()
    holder.join()
    assert e.value.code == "RATE_LIMITED" and e.value.details["degraded"] == "busy"
    assert e.value.details["action"] == "hold_line" and e.value.details["hold_line"] == "one_moment"
    assert "busy" in e.value.message.lower()
    fb = [r for r in lines() if r.get("degraded")]
    assert fb[0]["degraded"] == "busy" and fb[0]["kind"] == "tts" and fb[0]["lane"] == "live"


# -------------------------------------------------------------------------------------------------- backoff

def test_429_is_retried_with_exponential_backoff_and_jitter(monkeypatch):
    slept = []
    monkeypatch.setattr(gw, "_sleep", slept.append)
    monkeypatch.setattr(gw, "_random", lambda: 1.0)         # no jitter reduction
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise err("RATE_LIMITED", 429)
        return "ok"

    assert run(fn, lane="desk") == "ok"
    assert slept == [0.4, 0.8] and len(calls) == 3
    [rec] = lines()
    assert rec["ok"] and rec["attempts"] == 3 and rec["credits"] > 0


def test_jitter_scales_the_delay_between_half_and_full(monkeypatch):
    monkeypatch.setattr(gw, "_random", lambda: 0.0)
    assert gw.backoff_delay(0) == pytest.approx(0.2) and gw.backoff_delay(10) == pytest.approx(2.0)   # capped at 4 s, halved
    monkeypatch.setattr(gw, "_random", lambda: 1.0)
    assert gw.backoff_delay(10) == 4.0
    assert gw.backoff_delay(0, retry_after=3.0) == 3.0       # Retry-After wins when longer


def test_5xx_retries_are_bounded_then_the_error_says_what_to_do(monkeypatch):
    slept = []
    monkeypatch.setattr(gw, "_sleep", slept.append)
    monkeypatch.setattr(gw, "_now", lambda: 0.0)            # the deadline is never reached: only the retry count stops it

    def fn():
        raise err("PROVIDER_ERROR", 503)

    with pytest.raises(AppError) as e:                       # queued work retries patiently: 5 times
        run(fn, lane="desk")
    assert len(slept) == 5 and e.value.details["degraded"] == "server_error"
    assert e.value.details["action"] == "queued" and e.value.details["retry_after_s"] >= 5
    [rec] = lines()
    assert not rec["ok"] and rec["attempts"] == 6 and rec["degraded"] == "server_error" and rec["action"] == "queued"
    assert rec["credits"] == 0

    slept.clear()
    with pytest.raises(AppError) as e:                       # a live call retries twice, then is told to offer a callback
        run(fn, lane="live")
    assert len(slept) == 2 and e.value.details["action"] == "callback" and e.value.details["hold_line"] == "callback"


def test_a_live_request_stops_retrying_when_its_deadline_is_spent(monkeypatch):
    clock = [0.0]
    slept = []
    monkeypatch.setattr(gw, "_now", lambda: clock[0])
    monkeypatch.setattr(gw, "_sleep", lambda d: (slept.append(d), clock.__setitem__(0, clock[0] + d)))
    monkeypatch.setattr(gw, "_random", lambda: 1.0)
    monkeypatch.setenv("GNANI_LIVE_DEADLINE_S", "0.5")
    monkeypatch.setenv("GNANI_MAX_RETRIES", "5")
    calls = []

    def fn():
        calls.append(1)
        raise err("RATE_LIMITED", 429)

    with pytest.raises(AppError) as e:
        run(fn, lane="live")
    assert slept == [0.4] and len(calls) == 2                # 0.4 s fits in the 0.5 s deadline, the next 0.8 s does not
    assert e.value.details["action"] == "hold_line" and e.value.details["hold_line"] == "one_moment"


def test_desk_work_keeps_trying_for_much_longer_than_a_live_call(monkeypatch):
    assert gw.deadline_s("live") == 4 and gw.deadline_s("desk") == 60 and gw.deadline_s("bench") == 600
    assert gw.max_retries("live") == 2 and gw.max_retries("desk") == 5


def test_a_4xx_the_request_caused_is_not_retried_and_does_not_trip_the_breaker(monkeypatch):
    monkeypatch.setenv("GNANI_BREAKER_FAILS", "1")
    calls = []

    def fn():
        calls.append(1)
        raise err("PROVIDER_ERROR", 400)

    with pytest.raises(AppError):
        run(fn)
    assert len(calls) == 1 and G.breakers["tts"].snapshot()["state"] == "closed"
    assert lines()[0].get("degraded") is None


def test_timeouts_are_not_retried_on_the_live_lane(monkeypatch):
    calls = []

    def fn():
        calls.append(1)
        raise AppError("PROVIDER_UNAVAILABLE", "slow", 504, {"provider": "gnani"})

    with pytest.raises(AppError) as e:
        run(fn, lane="live")
    assert len(calls) == 1 and e.value.details["degraded"] == "timeout"


# -------------------------------------------------------------------------------------------- circuit breaker

def test_breaker_opens_refuses_probes_and_closes(monkeypatch):
    monkeypatch.setenv("GNANI_MAX_RETRIES", "0")
    monkeypatch.setenv("GNANI_BREAKER_FAILS", "3")
    clock = [1000.0]
    monkeypatch.setattr(gw, "_now", lambda: clock[0])
    calls = []

    def bad():
        calls.append(1)
        raise err("PROVIDER_ERROR", 500)

    for _ in range(3):
        with pytest.raises(AppError):
            run(bad)
    assert G.breakers["tts"].snapshot()["state"] == "open" and len(calls) == 3
    with pytest.raises(AppError) as e:                       # refused at once, the dead service is not called
        run(bad)
    assert len(calls) == 3 and e.value.details["degraded"] == "breaker_open" and e.value.code == "PROVIDER_UNAVAILABLE"
    assert "server_error" in e.value.message
    assert G.breakers["stt"].snapshot()["state"] == "closed"  # another kind is unaffected

    clock[0] += 31                                            # cooldown over: one probe goes through
    assert run(lambda: "back") == "back"
    assert G.breakers["tts"].snapshot()["state"] == "closed"
    reasons = [r.get("degraded") for r in lines() if r.get("degraded")]
    assert reasons.count("server_error") == 3 and reasons.count("breaker_open") == 1


def test_a_failed_probe_opens_the_breaker_again(monkeypatch):
    monkeypatch.setenv("GNANI_MAX_RETRIES", "0")
    monkeypatch.setenv("GNANI_BREAKER_FAILS", "1")
    clock = [0.0]
    monkeypatch.setattr(gw, "_now", lambda: clock[0])
    with pytest.raises(AppError):
        run(lambda: (_ for _ in ()).throw(err("PROVIDER_ERROR", 500)))
    clock[0] += 31
    with pytest.raises(AppError):
        run(lambda: (_ for _ in ()).throw(err("PROVIDER_ERROR", 500)))
    snap = G.breakers["tts"].snapshot()
    assert snap["state"] == "open" and snap["retry_in_s"] > 0


def test_out_of_credits_opens_every_breaker_for_longer(monkeypatch):
    monkeypatch.setenv("GNANI_MAX_RETRIES", "0")
    with pytest.raises(AppError) as e:
        run(lambda: (_ for _ in ()).throw(AppError("PROVIDER_CREDITS", "no credits", 402, {"status": 402})))
    assert e.value.details["degraded"] == "out_of_credits"
    snaps = {k: b.snapshot() for k, b in G.breakers.items()}
    assert all(s["state"] == "open" and s["reason"] == "out_of_credits" and s["retry_in_s"] > 200 for s in snaps.values())
    with pytest.raises(AppError) as e2:
        G.execute("stt", lambda: {}, units={"seconds": 1})
    assert e2.value.details["degraded"] == "breaker_open"


def test_without_a_key_the_gateway_does_nothing():
    calls = []
    assert G.execute("tts", lambda: calls.append(1) or "x", units={"chars": 5}, configured=False) == "x"
    assert lines() == []


# ---------------------------------------------------------------------------------------------------- cache

def test_a_repeated_request_is_a_cache_hit_with_no_slot_and_no_credits():
    calls = []
    key = gw.cache_key("tts", "namaste", "Nalini")
    first = run(lambda: calls.append(1) or b"AUDIO", key=key, size=len)
    again = run(lambda: calls.append(1) or b"OTHER", key=key, size=len)
    assert first == again == b"AUDIO" and len(calls) == 1
    a, b = lines()
    assert a["cache_hit"] is False and a["credits"] > 0 and b["cache_hit"] is True and b["credits"] == 0
    assert G.cache.stats()["hits"] == 1 and G.cache.stats()["hit_rate"] == 0.5


def test_cache_keys_depend_on_every_parameter_and_the_audio_bytes():
    k = gw.cache_key
    assert k("tts", "a", "v") == k("tts", "a", "v") and k("tts", "a", "v") != k("tts", "a", "w")
    assert k("stt", "hi", blobs=(b"x",)) != k("stt", "hi", blobs=(b"y",)) and k("stt", "x") != k("tts", "x")


def test_cache_returns_copies_so_a_caller_cannot_poison_it():
    key = gw.cache_key("stt", "x")
    G.execute("stt", lambda: {"text": "hello"}, units={"seconds": 1}, key=key, size=lambda r: 64)
    got = G.execute("stt", lambda: {"text": "new"}, units={"seconds": 1}, key=key, size=lambda r: 64)
    got["text"] = "mutated"
    assert G.execute("stt", lambda: {"text": "new"}, units={"seconds": 1}, key=key, size=lambda r: 64) == {"text": "hello"}


def test_cache_is_capped_in_bytes_and_evicts_least_recently_used(monkeypatch):
    monkeypatch.setenv("GNANI_CACHE_MB", "0.001")           # about 1,048 bytes
    for i in range(10):
        run(lambda: b"x" * 200, key=f"k{i}", size=len)
    st = G.cache.stats()
    assert st["bytes"] <= st["max_bytes"] and st["entries"] < 10
    assert G.cache.get("k0") is None and G.cache.get("k9") is not None
    run(lambda: b"x" * 5000, key="big", size=len)           # larger than a quarter of the cap: not cached
    assert G.cache.get("big") is None


def test_cache_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("GNANI_CACHE_MB", "0")
    calls = []
    for _ in range(2):
        run(lambda: calls.append(1) or b"a", key="same", size=len)
    assert len(calls) == 2


# -------------------------------------------------------------------------------------------- budget in the gateway

def test_desk_work_may_not_use_the_reserve_but_a_live_call_always_may(monkeypatch):
    monkeypatch.setenv("GNANI_CREDIT_BUDGET", "100")
    monkeypatch.setenv("GNANI_CREDIT_RESERVE_PCT", "20")
    ul.record_request(kind="tts", lane="live", credits=79.0, ok=True)        # 79 of the 80 usable credits are gone
    calls = []
    with pytest.raises(AppError) as e:
        run(lambda: calls.append(1) or b"a", lane="desk", units={"chars": 1000})   # 2.9 credits: 81.9 > 80
    assert e.value.details["degraded"] == "budget_reserve" and not calls
    assert "reserve" in e.value.message and "79 of 100" in e.value.message
    assert run(lambda: calls.append(1) or b"a", lane="live", units={"chars": 1000}) == b"a" and calls == [1]
    assert ul.LEDGER.spent() == pytest.approx(79.0 + 2.9)


# ------------------------------------------------------------------------------------------------------ WAV

def wav(riff, data_size, payload=b"\x01\x00" * 120, rate=24000):
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, rate, rate * 2, 2, 16)
    return b"RIFF" + struct.pack("<I", riff) + b"WAVE" + fmt + b"data" + struct.pack("<I", data_size) + payload


def sizes(b):
    return struct.unpack_from("<I", b, 4)[0], struct.unpack_from("<I", b, 40)[0]


@pytest.mark.parametrize("riff,data", [(0xFFFFFFFF, 0xFFFFFFFF), (0, 0), (0xFFFFFFFF, 0x7FFFFFFF)])
def test_placeholder_wav_sizes_are_rewritten(riff, data):
    fixed, changed = gw.fix_wav_header(wav(riff, data))
    assert changed and sizes(fixed) == (len(fixed) - 8, 240)
    assert gw.audio_seconds(wav(riff, data)) == pytest.approx(240 / 48000)


def test_a_good_wav_and_non_wav_bytes_are_left_alone():
    good = wav(36 + 240, 240)
    assert gw.fix_wav_header(good) == (good, False)
    assert gw.fix_wav_header(b"\xff\xfbMP3DATA" * 10) == (b"\xff\xfbMP3DATA" * 10, False)
    assert gw.audio_seconds(b"x" * 32000) == 2.0


def test_a_streamed_data_chunk_is_trimmed_to_whole_samples():
    fixed, _ = gw.fix_wav_header(wav(0xFFFFFFFF, 0xFFFFFFFF, payload=b"\x01\x00" * 100 + b"\x07"))
    assert sizes(fixed)[1] == 200


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "test-key-not-real-123")
    state = type("S", (), {})()
    state.requests, state.reply = [], lambda r: httpx.Response(200, json={"text": "ok"})

    def handler(request):
        state.requests.append(request)
        return state.reply(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gc, "_client", lambda: client)
    return state


def test_synthesize_rewrites_the_header_before_the_bytes_leave_the_client(api):
    api.reply = lambda r: httpx.Response(200, content=wav(0xFFFFFFFF, 0xFFFFFFFF), headers={"content-type": "audio/wav"})
    out = gc.synthesize("नमस्ते", voice="Nalini", language="hi-IN", sample_rate=24000, fmt="wav")
    assert sizes(out) == (len(out) - 8, 240)


# ------------------------------------------------------------------------- the three public client functions

def test_client_calls_are_cached_and_ledgered_with_units(api):
    api.reply = lambda r: httpx.Response(200, json={"text": "नमस्ते", "detected_language_code": "hi-IN"})
    audio = wav(36 + 96000, 96000, payload=b"\x00" * 96000, rate=48000)           # one second of audio
    a = gc.transcribe(audio, language="hi-IN")
    b = gc.transcribe(audio, language="hi-IN")
    c = gc.transcribe(audio, language="en-IN")                                       # other parameters: not a hit
    assert a == b and len(api.requests) == 2
    rows = lines()
    assert [r["cache_hit"] for r in rows] == [False, True, False]
    assert rows[0]["kind"] == "stt" and rows[0]["seconds"] == pytest.approx(1.0)
    assert rows[0]["credits"] == pytest.approx(0.0097) and rows[1]["credits"] == 0


def test_tts_credits_follow_characters_and_clone_follows_the_rate(api, monkeypatch):
    api.reply = lambda r: httpx.Response(200, content=wav(36 + 240, 240), headers={"content-type": "audio/wav"})
    gc.synthesize("a" * 500, voice="Nalini")
    [r] = lines()
    assert r["kind"] == "tts" and r["chars"] == 500 and r["credits"] == pytest.approx(1.45)
    monkeypatch.setenv("GNANI_RATE_CLONE_PER_REQUEST", "12")
    api.reply = lambda r: httpx.Response(200, json={"speaker_embedding": [0.1] * 768})
    gc.clone_embedding(b"clip")
    assert lines()[-1]["kind"] == "clone" and lines()[-1]["credits"] == 12


def test_a_retry_after_header_reaches_the_backoff(api, monkeypatch):
    slept = []
    monkeypatch.setattr(gw, "_sleep", slept.append)
    monkeypatch.setattr(gw, "_random", lambda: 0.0)
    responses = [httpx.Response(429, text="slow", headers={"Retry-After": "2"}), httpx.Response(200, json={"text": "ok"})]
    api.reply = lambda r: responses.pop(0)
    assert gc.transcribe(b"audio-bytes")["text"] == "ok"
    assert slept == [2.0]


# ------------------------------------------------------------------------------------------- scope and lanes

def test_the_scope_sets_the_lane_and_a_late_bind_attributes_earlier_requests():
    with gw.scope("live", user_id="u1"):
        run()                                       # lane comes from the scope; the conversation is not known yet
        gw.bind_conversation("conv-1")
        run()
    with gw.scope("bench"):
        run()
    run()                                           # outside any scope: the default lane
    rows = ul._resolve(ul.LEDGER.records())
    assert [r["lane"] for r in rows] == ["live", "live", "bench", "desk"]
    assert [r.get("conversation_id") for r in rows] == ["conv-1", "conv-1", None, None]
    assert rows[0]["user"] == "u1"


def test_the_default_lane_can_be_set_for_scripts(monkeypatch):
    monkeypatch.setenv("GNANI_DEFAULT_LANE", "bench")
    run()
    assert lines()[0]["lane"] == "bench"


def test_scope_survives_asyncio_to_thread():
    async def go():
        gw.begin_scope("live")
        await asyncio.to_thread(run)
    asyncio.run(go())
    assert lines()[0]["lane"] == "live"


# ------------------------------------------------------------------------------------------ realtime stream

def test_a_stream_holds_the_slot_for_its_session_and_is_ledgered(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "test-key-not-real-123")
    monkeypatch.setenv("GNANI_QUEUE_WAIT_LIVE_S", "0.05")

    class Sock:
        sent = []

        async def send(self, d):
            self.sent.append(d)

        async def close(self):
            pass

        def __aiter__(self):
            async def g():
                return
                yield
            return g()

    async def connect(url, additional_headers=None):
        return Sock()

    async def go():
        async with gc.StreamingRecognizer(sample_rate=8000, pace=False, connect=connect) as rec:
            assert G.slot.state()["busy"]
            with pytest.raises(AppError) as e:               # REST work meanwhile is told to fall back
                await asyncio.to_thread(run, lane="live")
            assert e.value.details["degraded"] == "busy"
            await rec.send_pcm(b"\x00" * 16000)              # 1 s of 8 kHz PCM16
        assert not G.slot.state()["busy"]
    asyncio.run(go())
    stream = [r for r in lines() if r["kind"] == "stt_stream"][0]
    assert stream["seconds"] == pytest.approx(1.02, abs=0.05) and stream["credits"] == pytest.approx(0.0074, abs=0.0005)


def test_a_stream_can_skip_the_slot_by_configuration(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "k")
    monkeypatch.setenv("GNANI_STREAM_HOLDS_SLOT", "off")

    async def connect(url, additional_headers=None):
        class S:
            async def close(self):
                pass
        return S()

    async def go():
        async with gc.StreamingRecognizer(connect=connect):
            assert not G.slot.state()["busy"]
    asyncio.run(go())


def test_a_paused_stt_refuses_a_new_stream(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "k")
    G.breakers["stt"].failure("server_error", trip=True)

    async def connect(url, additional_headers=None):
        raise AssertionError("must not connect")

    async def go():
        async with gc.StreamingRecognizer(connect=connect):
            pass
    with pytest.raises(AppError) as e:
        asyncio.run(go())
    assert e.value.details["degraded"] == "breaker_open"
    assert not G.slot.state()["busy"]


# ------------------------------------------------------------------- what a refused request tells its caller

@pytest.mark.parametrize("reason,live_action,live_line", [
    ("busy", "hold_line", "one_moment"), ("rate_limited", "hold_line", "one_moment"), ("timeout", "hold_line", "one_moment"),
    ("breaker_probe", "hold_line", "one_moment"), ("breaker_open", "callback", "callback"),
    ("server_error", "callback", "callback"), ("unreachable", "callback", "callback"),
    ("out_of_credits", "callback", "callback"), ("auth_rejected", "callback", "callback")])
def test_the_plan_for_a_live_call_depends_on_how_long_the_problem_lasts(reason, live_action, live_line):
    assert gw.plan_for("live", reason) == {"action": live_action, "hold_line": live_line}
    queued = gw.plan_for("desk", reason, retry_in_s=12)
    assert queued["action"] == "queued" and queued["retry_after_s"] == 12 and "hold_line" not in queued
    assert gw.plan_for("bench", reason)["retry_after_s"] == 5          # never "retry now"


def test_the_budget_holds_desk_work_back_instead_of_queueing_it():
    assert gw.plan_for("desk", "budget_reserve") == {"action": "held_back"}


def test_an_open_breaker_tells_a_live_call_to_offer_a_callback_and_desk_work_to_wait(monkeypatch):
    G.breakers["tts"].failure("server_error", trip=True)
    with pytest.raises(AppError) as live:
        run(lane="live")
    with pytest.raises(AppError) as desk:
        run(lane="desk")
    assert live.value.details["action"] == "callback" and live.value.details["hold_line"] == "callback"
    assert desk.value.details["action"] == "queued" and desk.value.details["retry_after_s"] >= 5
    actions = [r.get("action") for r in lines()]
    assert actions == ["callback", "queued"]


def test_no_message_or_detail_names_another_model_to_use():
    G.breakers["tts"].failure("server_error", trip=True)
    with pytest.raises(AppError) as e:
        run(lane="live")
    blob = json.dumps([e.value.message, e.value.details, lines(), gw.lane_status(), gw.state()]).lower()
    for word in ("local model", "another model", "other model", "fallback", "backup voice"):
        assert word not in blob


def test_the_lane_status_is_always_gnani_with_a_state(monkeypatch):
    s = gw.lane_status()
    assert s["live"]["stt"] == {"provider": "gnani", "name": "Gnani Prisma", "state": "needs_key",
                                "why": "No GNANI_API_KEY is set, so this cannot work yet."}
    monkeypatch.setenv("GNANI_API_KEY", "k")
    assert gw.lane_status()["desk"]["tts"]["state"] == "ready" and gw.lane_status()["desk"]["tts"]["name"] == "Gnani Timbre"
    G.breakers["tts"].failure("server_error", trip=True)
    live, desk = gw.lane_status()["live"]["tts"], gw.lane_status()["desk"]["tts"]
    assert live["state"] == "unavailable" and "callback" in live["why"] and "queued" in desk["why"]
    G.breakers["tts"].success()
    G.breakers["tts"].failure("busy", trip=True)
    assert gw.lane_status()["live"]["tts"]["state"] == "retrying"
    G.breakers["tts"].success()
    monkeypatch.setenv("GNANI_CREDIT_BUDGET", "10")
    ul.record_request(kind="tts", credits=9.0)
    assert gw.lane_status()["desk"]["stt"]["state"] == "held_back" and gw.lane_status()["live"]["stt"]["state"] == "ready"
