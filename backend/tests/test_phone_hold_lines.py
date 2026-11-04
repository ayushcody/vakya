"""When Gnani cannot answer on a live call, the caller hears a held line and never another voice: "one moment" and a second
try for a passing problem, "I will call you back" and a polite end when Gnani is down or the second try fails too."""
import json

import numpy as np

from core.errors import AppError
from routers import phone as px
from services import gnani_hold_lines
from tests.test_offline_agents import env, make_agent  # noqa: F401  (env is a fixture)
from tests.test_phone import STREAM_SID, feed, is_mark, pcm_silence, pcm_tone, read_until, rig, start_msg, token_for  # noqa: F401

LINE_PCM = {"one_moment": np.full(1600, 0.2, dtype=np.float32), "callback": np.full(3200, 0.3, dtype=np.float32)}


def refuse(line, reason="busy"):
    action = "hold_line" if line == "one_moment" else "callback"
    return AppError("RATE_LIMITED" if line == "one_moment" else "PROVIDER_UNAVAILABLE", "Gnani cannot answer", 503,
                    {"provider": "gnani", "degraded": reason, "action": action, "hold_line": line, "retry_after_s": 0.0})


def prepared(monkeypatch):
    """Held lines as the gateway's folder would give them: raw 16-bit PCM at 8 kHz, and a ledger entry per play."""
    played = []

    def get(line, language, *, phone=True, voice="default"):
        return (LINE_PCM[line] * 32767).astype("<i2").tobytes()

    def for_error(error, language, *, phone=True, voice="default"):
        line = error.details["hold_line"]
        played.append(line)
        return {"line": line, "text": gnani_hold_lines.text_of(line, language), "audio": get(line, language),
                "action": error.details.get("action")}
    monkeypatch.setattr(gnani_hold_lines, "get", get)
    monkeypatch.setattr(gnani_hold_lines, "for_error", for_error)
    return played


def media_bytes(msgs):
    import base64
    return sum(len(base64.b64decode(m["media"]["payload"])) for m in msgs if m["event"] == "media")


def test_a_passing_problem_plays_one_moment_and_the_turn_is_tried_again(rig, env, monkeypatch):
    played = prepared(monkeypatch)
    calls = {"n": 0}

    async def flaky_asr(audio_bytes, name, language=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise refuse("one_moment")
        return "When does the clinic open?"

    monkeypatch.setattr(env, "speech_to_text", flaky_asr)
    a = make_agent(rig.client)
    with rig.client.websocket_connect(f"{px.STREAM_PATH}/{token_for(a)}") as ws:
        ws.send_text(start_msg())
        read_until(ws, is_mark)                                   # the greeting is done
        feed(ws, pcm_silence(300) + pcm_tone(1200) + pcm_silence(900))
        msgs = read_until(ws, is_mark)
        ws.send_text(json.dumps({"event": "stop", "stream_sid": STREAM_SID, "stop": {"reason": "callended"}}))
    assert played == ["one_moment"] and calls["n"] == 2
    assert media_bytes(msgs) > 0
    assert any("When does the clinic open?" in q["messages"][-1]["content"] for q in rig.asked)   # the retry was answered


def test_gnani_down_plays_the_callback_line_and_ends_the_call(rig, env, monkeypatch):
    played = prepared(monkeypatch)

    async def down_asr(audio_bytes, name, language=None):
        raise refuse("callback", "breaker_open")

    monkeypatch.setattr(env, "speech_to_text", down_asr)
    a = make_agent(rig.client)
    with rig.client.websocket_connect(f"{px.STREAM_PATH}/{token_for(a)}") as ws:
        ws.send_text(start_msg())
        read_until(ws, is_mark)
        feed(ws, pcm_silence(300) + pcm_tone(1200) + pcm_silence(900))
        msgs = read_until(ws, is_mark)
        ws.send_text(json.dumps({"event": "stop", "stream_sid": STREAM_SID, "stop": {"reason": "callended"}}))
    assert played == ["callback"] and media_bytes(msgs) > 0
    assert rig.asked == []                                        # nothing reached the language model


def test_a_second_failure_after_one_moment_becomes_the_callback_line(rig, env, monkeypatch):
    played = prepared(monkeypatch)

    async def always_busy(audio_bytes, name, language=None):
        raise refuse("one_moment")

    monkeypatch.setattr(env, "speech_to_text", always_busy)
    a = make_agent(rig.client)
    with rig.client.websocket_connect(f"{px.STREAM_PATH}/{token_for(a)}") as ws:
        ws.send_text(start_msg())
        read_until(ws, is_mark)
        feed(ws, pcm_silence(300) + pcm_tone(1200) + pcm_silence(900))
        read_until(ws, is_mark)
        ws.send_text(json.dumps({"event": "stop", "stream_sid": STREAM_SID, "stop": {"reason": "callended"}}))
    assert played == ["one_moment", "callback"]
