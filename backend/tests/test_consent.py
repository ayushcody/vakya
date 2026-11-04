"""Consent gate on POST /api/voices/clone: a spoken consent recording is required and, when Gnani's recogniser is
available, must match the challenge phrase. Gnani (Prisma and the voice-clone embedding) is faked: nothing is sent."""
import pytest

CHALLENGE = "I consent to creating an AI version of my voice for the Vakya demonstration on October 14, 2026."
LOCAL = {"storage-mode": "local"}


@pytest.fixture()
def backend_mod(app_client, monkeypatch, tmp_path):
    from core.runtime import runtime as backend
    from services import gnani

    monkeypatch.setenv("CONSENT_MODE", "strict")  # these tests cover the read-aloud flow; tick mode is in test_consent_tick.py
    monkeypatch.setenv("GNANI_API_KEY", "test-key")
    embedded = []

    def fake_embedding(audio, **kw):
        embedded.append(len(audio))
        return [0.01] * 768

    monkeypatch.setattr(gnani.client, "clone_embedding", fake_embedding)
    monkeypatch.setattr(backend, "LOCAL_AUDIO_DIR", str(tmp_path))
    monkeypatch.setattr(backend, "LOCAL_VOICES_FILE", str(tmp_path / "voices.json"))
    backend.embedded = embedded
    backend.limiter.reset()  # clone is limited to 5/minute; each test starts with a fresh window
    return backend


def fake_prisma(heard_consent: str, heard_reference: str = "hello this is my reference voice"):
    """Prisma, faked: the consent recording is heard as `heard_consent`, anything else as `heard_reference`."""
    async def speech_to_text(audio_bytes, name, language=None):
        return heard_consent if name.startswith("consent") else heard_reference
    return speech_to_text


def clone(client, ref_wav, consent=True, consent_audio=True):
    wav = ref_wav.read_bytes()
    files = {"reference_audio": ("ref.wav", wav, "audio/wav")}
    if consent_audio:
        files["consent_audio"] = ("consent.wav", wav, "audio/wav")
    data = {"display_name": "Test", "language": "en", "consent_text": CHALLENGE,
            "consent_confirmed": "true" if consent else "false"}
    return client.post("/api/voices/clone", headers=LOCAL, data=data, files=files)


def test_clone_without_consent_recording_is_refused(app_client, backend_mod, ref_wav):
    r = clone(app_client, ref_wav, consent_audio=False)
    assert r.status_code == 400
    assert r.json()["code"] == "CONSENT_REQUIRED"


def test_clone_with_checkbox_false_is_refused(app_client, backend_mod, ref_wav):
    r = clone(app_client, ref_wav, consent=False)
    assert r.status_code == 400
    assert r.json()["code"] == "CONSENT_REQUIRED"


def test_mismatched_consent_phrase_is_refused(app_client, backend_mod, ref_wav, monkeypatch):
    monkeypatch.setattr(backend_mod, "speech_to_text", fake_prisma("what is the weather today"))
    r = clone(app_client, ref_wav)
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "CONSENT_MISMATCH"
    assert body["details"]["heard"] == "what is the weather today"
    assert body["details"]["score"] < backend_mod.CONSENT_MATCH_THRESHOLD


def test_matching_consent_clones_with_a_gnani_embedding(app_client, backend_mod, ref_wav, monkeypatch):
    heard = CHALLENGE.replace("14,", "14th,")  # typical ASR variation
    monkeypatch.setattr(backend_mod, "speech_to_text", fake_prisma(heard))
    r = clone(app_client, ref_wav)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["consent"]["verified"] is True
    assert body["consent"]["score"] >= backend_mod.CONSENT_MATCH_THRESHOLD
    assert body["consent_id"]
    assert body["engine"] == "timbre_clone" and body["mode"] == "embedding"
    assert len(backend_mod.embedded) == 1                       # one voice-clone request: the recording became an embedding


def test_consent_that_gnani_cannot_hear_right_now_is_kept_but_unverified(app_client, backend_mod, ref_wav, monkeypatch):
    from core.errors import AppError

    async def busy(audio_bytes, name, language=None):
        raise AppError("RATE_LIMITED", "Gnani AI rate limit reached.", 429, {"provider": "gnani"})

    monkeypatch.setattr(backend_mod, "speech_to_text", busy)
    r = clone(app_client, ref_wav)
    assert r.status_code == 200, r.text
    assert r.json()["consent"]["verified"] is False


def test_without_a_gnani_key_cloning_says_needs_a_gnani_key(app_client, backend_mod, ref_wav, monkeypatch):
    monkeypatch.delenv("GNANI_API_KEY", raising=False)
    r = clone(app_client, ref_wav)
    assert r.status_code == 503
    assert r.json()["code"] == "PROVIDER_NOT_CONFIGURED" and "Needs a Gnani key" in r.json()["error"]
    assert backend_mod.embedded == []


def test_consent_match_score():
    from core.runtime import runtime as backend
    assert backend.consent_match(CHALLENGE, CHALLENGE) == 1.0
    assert backend.consent_match(CHALLENGE, "") == 0.0
    assert backend.consent_match(CHALLENGE, "what is the weather today") < 0.2


# ---- the consent text itself is enforced -------------------------------------------------------------------

def clone_with_text(client, ref_wav, consent_text):
    """A clone request whose consent recording is paired with the given consent_text (None = field left out)."""
    wav = ref_wav.read_bytes()
    data = {"display_name": "Test", "language": "en", "consent_confirmed": "true"}
    if consent_text is not None:
        data["consent_text"] = consent_text
    return client.post("/api/voices/clone", headers=LOCAL, data=data,
                       files={"reference_audio": ("ref.wav", wav, "audio/wav"),
                              "consent_audio": ("consent.wav", wav, "audio/wav")})


def nothing_saved(backend_mod, tmp_path):
    return not (tmp_path / "voices.json").exists() and not list(tmp_path.glob("*.wav"))


def test_leaving_out_the_consent_text_does_not_skip_the_check(app_client, backend_mod, ref_wav, tmp_path, monkeypatch):
    """Regression: with no consent_text any audio was accepted as 'consent' and the voice was created unverified."""
    asr_calls = []

    async def counting(audio_bytes, name, language=None):
        asr_calls.append(name)
        return ""

    monkeypatch.setattr(backend_mod, "speech_to_text", counting)
    for missing in (None, "", "   "):
        r = clone_with_text(app_client, ref_wav, missing)
        assert (r.status_code, r.json()["code"]) == (400, "CONSENT_REQUIRED"), missing
    assert asr_calls == [] and nothing_saved(backend_mod, tmp_path)


def test_the_text_must_be_a_consent_statement_not_any_matching_sentence(app_client, backend_mod, ref_wav, tmp_path, monkeypatch):
    """Regression: the server verified the recording against client-supplied text, so audio saying 'the weather is
    sunny' paired with the text 'the weather is sunny' counted as consent."""
    weather = "The weather today is sunny with a light breeze."
    monkeypatch.setattr(backend_mod, "speech_to_text", fake_prisma(weather))  # the recording does say exactly this text
    r = clone_with_text(app_client, ref_wav, weather)
    assert (r.status_code, r.json()["code"]) == (422, "CONSENT_INVALID")
    assert "I consent to creating an AI version of my voice" in r.json()["details"]["expected"]
    # the words in the wrong order are not the statement either
    r = clone_with_text(app_client, ref_wav, "voice my of version AI an creating to consent I")
    assert (r.status_code, r.json()["code"]) == (422, "CONSENT_INVALID")
    assert nothing_saved(backend_mod, tmp_path)


@pytest.mark.parametrize("text", [
    CHALLENGE,                                                                    # what the wizard sends (US date)
    "I consent to creating an AI version of my voice for the Vakya demonstration on 14 October 2026.",  # UK date
    "i consent to creating an ai version of my voice for the vakya demo",      # case and punctuation do not matter
    "Today, I consent to creating an AI version of my voice, for any use I approve.",   # extra words around it
])
def test_real_consent_sentences_are_accepted_whatever_the_date_format(app_client, backend_mod, ref_wav, monkeypatch, text):
    monkeypatch.setattr(backend_mod, "speech_to_text", fake_prisma(text))
    r = clone_with_text(app_client, ref_wav, text)
    assert r.status_code == 200, r.text
    assert r.json()["consent"]["verified"] is True
