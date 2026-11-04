"""services/gnani_hold_lines.py: the pre-rendered Timbre lines a live call plays when Gnani cannot answer."""
import json
import struct
import time

import httpx
import pytest

from core.errors import AppError
from services import gnani_client as gc
from services import gnani_gateway as gw
from services import gnani_hold_lines as hl
from services import usage_ledger as ul


@pytest.fixture()
def timbre(monkeypatch):
    """A fake Timbre: returns audio whose bytes name the text, and records every request."""
    monkeypatch.setenv("GNANI_API_KEY", "test-key-not-real-123")
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        fmt = body["audio_config"]["format"]
        payload = body["text"].encode()
        if fmt == "wav":
            fmtc = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 24000, 48000, 2, 16)
            return httpx.Response(200, content=b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE" + fmtc + b"data"
                                  + struct.pack("<I", 0xFFFFFFFF) + payload, headers={"content-type": "audio/wav"})
        return httpx.Response(200, content=payload, headers={"content-type": "application/octet-stream"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gc, "_client", lambda: client)
    return seen


def test_the_lines_are_in_the_languages_with_text_and_say_what_is_needed():
    assert set(hl.LINES) == {"one_moment", "callback"} and set(hl.LINES["callback"]) == {"en", "hi", "mr", "hi-en"}
    assert "call you back" in hl.text_of("callback", "en") and "one moment" in hl.text_of("one_moment", "en").lower()
    assert hl.normalise("hinglish") == "hi-en" and hl.normalise("mr-IN") == "mr" and hl.normalise("ta") == "en"


def test_nothing_is_prepared_until_timbre_has_spoken_them(timbre):
    assert hl.get("callback", "en") is None
    st = hl.status()
    assert st["complete"] is False and st["present"] == 0 and st["expected"] == 8 and "callback/en/phone" in st["missing_summary"]


def test_prepare_renders_each_line_for_phone_and_web_and_keeps_them_on_disk(timbre, tmp_path):
    out = hl.prepare()
    assert out["made"] == 8 and out["failed"] == [] and out["complete"] is True
    phone = hl.get("callback", "en", phone=True)
    web = hl.get("callback", "en", phone=False)
    assert phone == hl.text_of("callback", "en").encode()                       # raw 8 kHz PCM as Timbre sent it
    assert web[:4] == b"RIFF" and struct.unpack_from("<I", web, 4)[0] == len(web) - 8     # the header placeholder was fixed
    assert {(b["audio_config"]["format"], b["audio_config"]["sample_rate"]) for b in timbre} == {("raw", 8000), ("wav", 24000)}
    assert {b["language"] for b in timbre} == {"en-IN", "hi-IN"} and all(b["voice"] for b in timbre)
    assert len(list((tmp_path / "hold_lines").iterdir())) == 8
    hl.reset()                                                                   # a restart: read back from disk
    assert hl.get("callback", "en", phone=True) == phone
    ledger = [r for r in ul.LEDGER.records() if r.get("kind") == "tts"]
    assert len(ledger) == 8 and all(r["lane"] == "desk" for r in ledger)         # prepared on the desk lane, never a live call's


def test_prepare_only_makes_what_is_missing_and_survives_one_failure(timbre, monkeypatch):
    hl.prepare()
    n = len(timbre)
    assert hl.prepare()["made"] == 0 and len(timbre) == n
    for p in hl.directory().glob("callback.hi.*"):
        p.unlink()
    monkeypatch.setenv("GNANI_MAX_RETRIES", "0")
    monkeypatch.setenv("GNANI_CACHE_MB", "0")          # otherwise the cache would answer without calling Timbre
    real = gc._synthesize_raw
    monkeypatch.setattr(gc, "_synthesize_raw", lambda text, *a, **k: (_ for _ in ()).throw(AppError("PROVIDER_ERROR", "x", 502, {"status": 500}))
                        if text == hl.LINES["callback"]["hi"] else real(text, *a, **k))
    out = hl.prepare(force=False)
    assert out["made"] == 0 and len(out["failed"]) == 2 and out["complete"] is False


def test_the_languages_to_prepare_can_be_chosen(timbre, monkeypatch):
    monkeypatch.setenv("GNANI_HOLD_LINE_LANGUAGES", "mr, hinglish, xx")
    assert hl.languages() == ["mr", "hi-en", "en"]
    assert hl.status()["expected"] == 12


def error(reason, action, line):
    return AppError("RATE_LIMITED", "x", 429, {"provider": "gnani", "degraded": reason, "action": action, "hold_line": line})


def test_a_refused_live_request_gets_its_line_and_the_ledger_says_it_was_played(timbre):
    hl.prepare()
    with gw.scope("live", conversation_id="call-7"):
        one = hl.for_error(error("busy", "hold_line", "one_moment"), "hi", phone=True)
        cb = hl.for_error(error("breaker_open", "callback", "callback"), "en", phone=False)
    assert one["line"] == "one_moment" and one["audio"] == hl.text_of("one_moment", "hi").encode() and "क्षण" in one["text"]
    assert cb["line"] == "callback" and cb["audio"][:4] == b"RIFF" and cb["action"] == "callback"
    played = [r for r in ul.LEDGER.records() if r.get("kind") == "hold_line"]
    assert [r["action"] for r in played] == ["hold_line_played", "callback_offered"]
    assert all(r["conversation_id"] == "call-7" and r["credits"] == 0 and r["lane"] == "live" for r in played)
    assert played[0]["degraded"] == "busy"


def test_a_line_that_is_not_prepared_still_returns_its_text_for_the_callback(timbre):
    out = hl.for_error(error("breaker_open", "callback", "callback"), "en")
    assert out["audio"] is None and "call you back" in out["text"]


def test_errors_without_a_plan_play_nothing(timbre):
    assert hl.for_error(AppError("VALIDATION_ERROR", "x", 422), "en") is None
    assert hl.for_error(AppError("RATE_LIMITED", "x", 429, {"action": "queued", "degraded": "busy"}), "en") is None
    assert [r for r in ul.LEDGER.records() if r.get("kind") == "hold_line"] == []


def test_the_summary_counts_the_played_lines_per_call_and_does_not_count_them_as_requests(timbre):
    with gw.scope("live", conversation_id="call-7"):
        gw.GATEWAY.breakers["tts"].failure("server_error", trip=True)
        with pytest.raises(AppError) as e:
            gc.synthesize("hello", voice="Nalini")
        hl.for_error(e.value, "en")
    s = ul.summarize(ul.LEDGER.records(), days=7)
    assert s["degraded"]["count"] == 1 and s["degraded"]["hold_lines_played"] == 1
    assert s["degraded"]["by_action"] == {"callback": 1} and s["degraded"]["by_reason"] == {"breaker_open": 1}
    call = ul.calls_map(ul.LEDGER.records())["call-7"]
    assert call["degraded"] == 1 and set(call["actions"]) == {"callback", "callback_offered"} and call["degraded_reasons"] == ["breaker_open"]


def test_the_first_served_voice_request_prepares_the_lines_in_the_background(timbre, monkeypatch):
    monkeypatch.setenv("GNANI_PREPARE_HOLD_LINES", "on")
    gc.synthesize("hello", voice="Nalini")
    for _ in range(100):
        if hl.status()["complete"]:
            break
        time.sleep(0.05)
    assert hl.status()["complete"] is True
    n = len(timbre)
    gc.synthesize("hello again", voice="Nalini")      # only once per process
    time.sleep(0.1)
    assert len(timbre) == n + 1


def test_the_hold_line_texts_name_no_other_model():
    blob = json.dumps(hl.LINES, ensure_ascii=False).lower()
    assert not any(w in blob for w in ("another model", "local model", "other voice"))
