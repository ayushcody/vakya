"""Helpers shared by the turn tests: fake speech recognition and speech, and one spoken turn through the endpoint."""
import numpy as np

from tests.test_offline_agents import LOCAL
from tests.voice_fakes import fake_timbre


def fake_speech(env, monkeypatch):
    async def fake_asr(audio_bytes, name, language=None):
        return "What is the refund window?"

    monkeypatch.setattr(env, "speech_to_text", fake_asr)
    fake_timbre(monkeypatch)


def speak(client, agent_id, wav):
    r = client.post(f"/api/conversations/{agent_id}/turn", headers=LOCAL, files={"audio": ("rec.wav", wav, "audio/wav")})
    assert r.status_code == 200, r.text
    return r.json()
