"""Gnani AI wired into the backend (services/gnani.py + backend.py), with the client faked: Prisma's inputs, the Timbre
engine (registration, routing, phone and Studio audio, no fallback voice) and Timbre voice-clone embeddings. No test reaches
api.vachana.ai."""
import asyncio
import io
import json
import types

import numpy as np
import pytest
import soundfile as sf

from core.errors import AppError
from services import gnani, licences
from services import phone_audio as pa


@pytest.fixture()
def backend_mod(app_client):
    from core.runtime import runtime as backend

    backend.limiter.reset()
    return backend


@pytest.fixture()
def key(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "test-key-not-real")


def wav_bytes(seconds=0.2, sr=16000):
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(sr * seconds), dtype=np.float32), sr, format="WAV")
    return buf.getvalue()


# ------------------------------------------------------------------------------------------ language mapping

def test_language_codes_for_the_agents_languages():
    assert gnani.stt_language_code(["hi"]) == "hi-IN"
    assert gnani.stt_language_code(["hi", "en", "mr"]) == "hi-IN,en-IN,mr-IN"
    assert gnani.stt_language_code(["hinglish"]) == "hi-IN,en-IN"
    assert gnani.stt_language_code(["en-US", "hi_IN"]) == "en-IN,hi-IN"          # regions are replaced by -IN


def test_language_code_limits_and_unknowns():
    assert gnani.stt_language_code(["hi", "mr", "bn", "ta", "te", "kn"]) == "hi-IN,mr-IN,bn-IN,ta-IN"   # at most four
    assert gnani.stt_language_code([]) == "indic-auto" and gnani.stt_language_code(None) == "indic-auto"
    assert gnani.stt_language_code(["xx", "fr"]) == "indic-auto"
    assert gnani.stt_language_code(["hi", "en"], model="prisma-v2.5") == "indic-auto"   # only v3.0 takes a list
    assert gnani.stt_language_code(["hi"], model="prisma-v2.5") == "hi-IN"


def test_detected_language_maps_back_to_two_letters():
    assert gnani.two_letter("hi-IN") == "hi" and gnani.two_letter("EN-in") == "en"
    assert gnani.two_letter("xx-IN") is None and gnani.two_letter(None) is None


def test_tts_language_codes():
    assert gnani.tts_language_code("hi") == "hi-IN" and gnani.tts_language_code("mr") == "mr-IN"
    assert gnani.tts_language_code("hinglish") == "hi-en" and gnani.tts_language_code("fr") == "auto"


def test_voices_are_configurable(monkeypatch):
    assert gnani.voice_for("hi") == "Nalini" and gnani.voice_for("en") == "Kaveri" and gnani.voice_for("hinglish") == "Poorvi"
    monkeypatch.setenv("GNANI_VOICES", "Asha:hi, Ravi:en ,Lata:mr")
    assert gnani.voices() == [("Asha", "hi"), ("Ravi", "en"), ("Lata", "mr")]
    assert gnani.voice_for("mr") == "Lata" and gnani.voice_for("ta") == "Asha"   # no Tamil voice: the first one


def test_keyterms_merge_env_and_agent_terms(monkeypatch):
    monkeypatch.setenv("GNANI_KEYTERMS", "OPD, UPI\nPAN")
    assert gnani.env_keyterms() == ["OPD", "UPI", "PAN"]
    ents = [types.SimpleNamespace(text="Sunrise Clinic"), types.SimpleNamespace(text="opd")]
    assert gnani.merge_keyterms(gnani.env_keyterms(), gnani.keyterms_from_entities(ents)) == [
        "OPD", "UPI", "PAN", "Sunrise Clinic"]


# --------------------------------------------------------------------------------------------- the key

def test_there_are_no_provider_switches():
    for name in ("asr_provider", "tts_provider", "timbre_active"):
        assert not hasattr(gnani, name)


def test_without_a_key_listening_and_speaking_say_needs_a_gnani_key(backend_mod):
    with pytest.raises(AppError) as exc:
        asyncio.run(backend_mod.speech_to_text(wav_bytes(), "turn", "hi"))
    assert exc.value.code == "PROVIDER_NOT_CONFIGURED" and exc.value.status == 503 and "Needs a Gnani key" in exc.value.message
    with pytest.raises(AppError) as exc:
        gnani.synth("नमस्ते", "hi")
    assert exc.value.code == "PROVIDER_NOT_CONFIGURED" and "Needs a Gnani key" in exc.value.message
    assert "Needs a Gnani key" in gnani.engine_unavailable_reason()


# ------------------------------------------------------------------------------------------------------- ASR

@pytest.fixture()
def gnani_asr(backend_mod, key, monkeypatch):
    """Gnani ASR with a fake client: calls records the (audio, kwargs) of each transcription."""
    calls = []

    def fake(audio, **kw):
        calls.append((audio, kw))
        return {"text": "नमस्ते", "language": "hi-IN", "provider": "gnani"}

    monkeypatch.setattr(gnani.client, "transcribe", fake)
    return calls


def test_gnani_asr_sends_codemix_and_the_agents_hints(backend_mod, gnani_asr, monkeypatch):
    monkeypatch.setenv("GNANI_KEYTERMS", "OPD")
    qu = backend_mod.qu_module()
    hints = {"prompt": None, "verbose": True, "asr_languages": ["hi", "en"], "keyterms": ["Dr. Meera"],
             "previous_text": "mujhe kal ka time chahiye"}
    token = qu.ASR_CTX.set(hints)
    try:
        text = asyncio.run(backend_mod.speech_to_text(wav_bytes(), "turn", None))
    finally:
        qu.ASR_CTX.reset(token)
    assert text == "नमस्ते"
    audio, kw = gnani_asr[0]
    assert audio.startswith(b"RIFF")
    assert kw["language"] == "hi-IN,en-IN" and kw["fmt"] == "codemix" and kw["model"] == "prisma-v3.0"
    assert kw["keyterms"] == ["OPD", "Dr. Meera"] and kw["previous_text"] == "mujhe kal ka time chahiye"
    assert hints["detected_language"] == "hi" and hints["provider"] == "gnani" and hints["segments"] is None


def test_gnani_asr_without_turn_hints_uses_the_given_language_or_auto(backend_mod, gnani_asr):
    asyncio.run(backend_mod.speech_to_text(wav_bytes(), "consent", "en"))
    asyncio.run(backend_mod.speech_to_text(wav_bytes(), "whatsapp", None))
    assert gnani_asr[0][1]["language"] == "en-IN" and gnani_asr[1][1]["language"] == "indic-auto"


def test_gnani_asr_model_and_format_come_from_the_environment(backend_mod, gnani_asr, monkeypatch):
    monkeypatch.setenv("GNANI_STT_MODEL", "prisma-v2.5")
    monkeypatch.setenv("GNANI_STT_FORMAT", "transcribe")
    asyncio.run(backend_mod.speech_to_text(wav_bytes(), "turn", "hi"))
    kw = gnani_asr[0][1]
    assert kw["model"] == "prisma-v2.5" and kw["fmt"] == "transcribe" and kw["language"] == "hi-IN"


def test_a_webm_recording_is_re_encoded_to_wav_for_gnani(backend_mod, gnani_asr, monkeypatch):
    async def fake_load(audio, sr=16000):
        return np.zeros(sr, dtype=np.float32), sr
    monkeypatch.setattr(backend_mod, "sniff_audio_kind", lambda b: "webm")
    monkeypatch.setattr(backend_mod, "load_audio_bytes", fake_load)
    asyncio.run(backend_mod.speech_to_text(b"webm-bytes", "turn", "hi"))
    assert gnani_asr[0][0].startswith(b"RIFF")


@pytest.mark.parametrize("code,status", [("PROVIDER_CREDITS", 402), ("RATE_LIMITED", 429)])
def test_a_gnani_failure_reaches_the_caller_with_its_plan_and_never_another_recogniser(backend_mod, key, monkeypatch, code, status):
    def boom(audio, **kw):
        raise AppError(code, "nope", status, {"hold_line": "one_moment", "action": "hold_line", "degraded": "rate_limited"})
    monkeypatch.setattr(gnani.client, "transcribe", boom)
    with pytest.raises(AppError) as exc:
        asyncio.run(backend_mod.speech_to_text(wav_bytes(), "turn", "hi"))
    assert exc.value.code == code and exc.value.details["hold_line"] == "one_moment"


def test_turn_hints_carry_languages_keyterms_and_the_previous_utterance(backend_mod):
    qu = backend_mod.qu_module()
    ents = [types.SimpleNamespace(text="Shivajinagar", kind="place", count=2)]
    profile = types.SimpleNamespace(lexicon=types.SimpleNamespace(entities=ents), prompt=None)
    original = qu.get_profile
    qu.get_profile = lambda *a, **k: profile
    try:
        run = types.SimpleNamespace(agent={"supported_languages": ["hi", "en"]}, user_id="u", storage_mode="local",
                                    history=[{"role": "user", "text_content": "pehla sawaal"},
                                             {"role": "agent", "text_content": "jawab"},
                                             {"role": "user", "text_content": "doosra sawaal"}])
        hints = {}
        backend_mod.gnani_turn_hints(run, hints, qu)
        assert hints["asr_languages"] == ["hi", "en"] and hints["previous_text"] == "doosra sawaal"
        assert hints["keyterms"] == ["Shivajinagar"] and hints["expecting"] == "open" and hints["speaker_names"] == []
        assert hints["substitution_map"] == [] and "front desk" in hints["topic"]
        run.asr_previous_user_text = "phone caller said this"        # the phone call tracks it itself
        backend_mod.gnani_turn_hints(run, hints, qu)
        assert hints["previous_text"] == "phone caller said this"
    finally:
        qu.get_profile = original


# ------------------------------------------------------------------------------ engine: registration, routing

def test_timbre_is_registered_with_the_gnani_licence(backend_mod):
    reg = backend_mod.ENGINE_REGISTRY
    assert reg["timbre"]["name"] == "Timbre (Gnani)" and reg["timbre_clone"]["type"] == "clone"
    assert {"hi", "mr", "bn", "ta", "te", "kn", "ml", "gu", "pa", "en", "hi-en"} <= reg["timbre"]["languages"]
    assert licences.licence_of("timbre") == "Gnani AI API terms" == licences.licence_of("timbre_clone")
    assert licences.paid_use_ok("timbre")


def test_engine_list_flags_timbre_availability(app_client, monkeypatch):
    rows = {e["id"]: e for e in app_client.get("/api/engines").json()}
    assert rows["timbre"]["name"] == "Timbre (Gnani)" and rows["timbre"]["available"] is False
    assert "GNANI_API_KEY" in rows["timbre"]["unavailable_reason"]
    assert rows["timbre"]["licence"] == "Gnani AI API terms"
    monkeypatch.setenv("GNANI_API_KEY", "k")
    rows = {e["id"]: e for e in app_client.get("/api/engines").json()}
    assert rows["timbre"]["available"] is True and rows["timbre_clone"]["available"] is True


def test_routing_is_timbre_for_every_language_with_or_without_a_key(backend_mod, key):
    for lang in ("hi", "mr", "ta", "bn", "en"):
        assert backend_mod.route_engine(lang, "fast", False) == "timbre"
        assert backend_mod.route_engine(lang, "premium", True) == "timbre_clone"


# ----------------------------------------------------------------------------------- engine: audio formats

@pytest.fixture()
def synth_calls(monkeypatch):
    calls = []

    def fake(text, **kw):
        calls.append((text, kw))
        return b"\x00\x10" * 800 if kw["fmt"] == "raw" else wav_bytes(0.1, kw["sample_rate"])

    monkeypatch.setattr(gnani.client, "synthesize", fake)
    return calls


def test_studio_audio_is_24k_wav_and_phone_audio_is_8k_raw_pcm(key, synth_calls):
    x, sr = gnani.synth("नमस्ते", "hi")
    assert sr == 24000 and len(x) == 2400
    kw = synth_calls[0][1]
    assert kw["fmt"] == "wav" and kw["sample_rate"] == 24000 and kw["voice"] == "Nalini" and kw["language"] == "hi-IN"
    x, sr = gnani.synth("नमस्ते", "hi", phone=True)
    assert sr == 8000 and len(x) == 800 and x.dtype == np.float32
    kw = synth_calls[1][1]
    assert kw["fmt"] == "raw" and kw["sample_rate"] == 8000
    # the phone line carries PCM16 at the call's rate: Timbre's 8 kHz audio needs no resampling at 8 kHz
    assert len(pa.to_phone_pcm(x, sr, 8000)) == 800 * 2


def test_a_phone_plan_asks_timbre_for_8k(backend_mod, key, synth_calls):
    plan = backend_mod.TtsPlan("timbre", "hi", False, phone=True)
    wav, sr = asyncio.run(backend_mod.turn_synth(plan, "नमस्ते"))
    assert sr == 8000 and synth_calls[0][1]["fmt"] == "raw"
    wav, sr = asyncio.run(backend_mod.turn_synth(backend_mod.TtsPlan("timbre", "hi", False), "नमस्ते"))
    assert sr == 24000 and synth_calls[1][1]["fmt"] == "wav"


def test_a_turn_routed_to_timbre_gets_a_phone_plan_on_calls(backend_mod, key):
    run = types.SimpleNamespace(agent={"voice_id": None}, user_id="u", storage_mode="local", source="phone")
    plan = asyncio.run(backend_mod.turn_voice(run, "hi", False))
    assert plan.engine_key == "timbre" and plan.phone is True
    run.source = None
    assert asyncio.run(backend_mod.turn_voice(run, "hi", False)).phone is False


def test_a_fallback_reply_is_still_spoken_by_timbre(backend_mod, key, monkeypatch):
    run = types.SimpleNamespace(agent={"voice_id": None}, user_id="u", storage_mode="local", source="phone")
    assert asyncio.run(backend_mod.turn_voice(run, "hi", True)).engine_key == "timbre"


def test_a_voice_preview_goes_through_timbre_and_the_receipt_names_it(backend_mod, key, synth_calls, monkeypatch):
    seen = {}

    def apply(**kw):
        seen.update(kw)
        return kw["wav_bytes"], {"receipt_id": "r1"}

    monkeypatch.setattr(backend_mod.provenance, "apply_provenance", apply)
    wav_bytes, receipt_id = asyncio.run(backend_mod.preview_speech("नमस्ते", "", "hi", "fast", "u1", "local"))
    assert seen["engine"] == "timbre" and wav_bytes.startswith(b"RIFF") and receipt_id == "r1"
    assert synth_calls[0][1]["sample_rate"] == 24000 and synth_calls[0][1]["fmt"] == "wav"
    assert licences.licence_of(seen["engine"]) == "Gnani AI API terms"


@pytest.mark.parametrize("lang", ["hi", "ta", "en"])
def test_when_gnani_cannot_speak_the_error_goes_up_and_no_other_voice_does(backend_mod, key, monkeypatch, lang):
    def boom(*a, **k):
        raise AppError("PROVIDER_CREDITS", "no credits", 402)
    monkeypatch.setattr(gnani.client, "synthesize", boom)
    with pytest.raises(AppError) as exc:
        asyncio.run(backend_mod.turn_synth(backend_mod.TtsPlan("timbre", lang, False), "x"))
    assert exc.value.code == "PROVIDER_CREDITS"


# -------------------------------------------------------------------------------- cloning: embedding storage

@pytest.fixture()
def voices(backend_mod, key, monkeypatch, tmp_path):
    """Local voice store in a temp dir, the consent and ASR steps faked, Gnani's embedding endpoint faked."""
    state = {"embedded": [], "embedding": [0.25] * 768, "fail": None}

    async def ok(*a, **k):
        return {"verified": True}

    async def consent(*a, **k):
        return "consent-1"

    def embed(audio, **kw):
        state["embedded"].append(audio)
        if state["fail"]:
            raise state["fail"]
        return state["embedding"]

    monkeypatch.setattr(backend_mod, "LOCAL_AUDIO_DIR", str(tmp_path))
    monkeypatch.setattr(backend_mod, "LOCAL_VOICES_FILE", str(tmp_path / "voices.json"))
    monkeypatch.setattr(backend_mod, "verify_consent", ok)
    monkeypatch.setattr(backend_mod, "record_consent", consent)
    monkeypatch.setattr(backend_mod, "storage_mode_of", lambda request: "local")
    monkeypatch.setattr(gnani.client, "clone_embedding", embed)
    state["tmp"] = tmp_path
    return state


def clone(backend_mod, language="hi"):
    return asyncio.run(backend_mod.clone_voice_core(types.SimpleNamespace(), "u1", wav_bytes(1.0), b"consent",
                                                     "say this", "", language, "Meera", ""))


def test_cloning_in_hindi_stores_a_gnani_embedding_with_the_voice(backend_mod, voices):
    out = clone(backend_mod)
    assert out["engine"] == "timbre_clone"
    row = json.loads((voices["tmp"] / "voices.json").read_text())[0]
    assert row["engine"] == "timbre_clone" and row["timbre_embedding"] is True and row["timbre_embedding_dims"] == 768
    stored = json.loads((voices["tmp"] / f"{out['id']}.timbre.json").read_text())
    assert stored["speaker_embedding"] == [0.25] * 768
    assert voices["embedded"][0].startswith(b"RIFF")
    emb = asyncio.run(backend_mod.timbre_embedding_load(out["id"], "u1", "local", row))
    assert emb == [0.25] * 768


def test_an_embedding_failure_is_the_error_of_the_clone_request(backend_mod, voices):
    voices["fail"] = AppError("PROVIDER_CREDITS", "no credits", 402)
    with pytest.raises(AppError) as exc:
        clone(backend_mod)
    assert exc.value.code == "PROVIDER_CREDITS"
    assert not list(voices["tmp"].glob("*.timbre.json")) and not (voices["tmp"] / "voices.json").exists()


@pytest.mark.parametrize("language", ["en", "hi", "mr"])
def test_every_language_is_cloned_by_gnani(backend_mod, voices, language):
    out = clone(backend_mod, language)
    assert out["engine"] == "timbre_clone" and len(voices["embedded"]) == 1


def test_a_saved_voice_speaks_with_its_embedding(backend_mod, voices, synth_calls):
    out = clone(backend_mod)
    run = types.SimpleNamespace(agent={"voice_id": out["id"]}, user_id="u1", storage_mode="local", source="phone")
    plan = asyncio.run(backend_mod.turn_voice(run, "hi", False))
    assert plan.engine_key == "timbre_clone" and plan.embedding == [0.25] * 768 and plan.used_voice == out["id"]
    asyncio.run(backend_mod.turn_synth(plan, "नमस्ते"))
    kw = synth_calls[0][1]
    assert kw["speaker_embedding"] == [0.25] * 768 and kw["voice"] is None and kw["fmt"] == "raw"   # phone: 8 kHz raw


def test_a_voice_without_an_embedding_gets_one_on_first_use(backend_mod, voices):
    out = clone(backend_mod)
    (voices["tmp"] / f"{out['id']}.timbre.json").unlink()
    voices["embedded"].clear()
    engine, emb, name = asyncio.run(backend_mod.resolve_timbre_clone("hi", "fast", out["id"], "u1", "local"))
    assert engine == "timbre_clone" and emb == [0.25] * 768 and name == "Meera" and len(voices["embedded"]) == 1
    asyncio.run(backend_mod.resolve_timbre_clone("hi", "fast", out["id"], "u1", "local"))
    assert len(voices["embedded"]) == 1       # stored: not embedded again


def test_the_embedding_is_deleted_with_the_voice(backend_mod, voices):
    out = clone(backend_mod)
    path = voices["tmp"] / f"{out['id']}.timbre.json"
    assert path.exists()
    asyncio.run(backend_mod.delete_voice_core(out["id"], "u1", "local"))
    assert not path.exists()


def test_an_unknown_voice_is_a_404(backend_mod, voices):
    with pytest.raises(AppError) as exc:
        asyncio.run(backend_mod.resolve_timbre_clone("hi", "fast", "missing-voice", "u1", "local"))
    assert exc.value.code == "VOICE_NOT_FOUND"


def test_a_voice_preview_for_a_saved_voice_carries_its_embedding(backend_mod, voices, synth_calls, monkeypatch):
    out = clone(backend_mod)
    seen = {}
    monkeypatch.setattr(backend_mod.provenance, "apply_provenance",
                        lambda **kw: (seen.update(kw) or kw["wav_bytes"], {"receipt_id": "r1"}))
    asyncio.run(backend_mod.preview_speech("नमस्ते", out["id"], "hi", "fast", "u1", "local"))
    assert synth_calls[0][1]["speaker_embedding"] == [0.25] * 768 and synth_calls[0][1]["fmt"] == "wav"
    assert seen["engine"] == "timbre_clone" and seen["voice_name"] == "Meera" and seen["voice_id"] == out["id"]
