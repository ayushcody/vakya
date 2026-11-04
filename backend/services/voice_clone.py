"""Cloning a voice with consent (a spoken sentence checked by speech recognition, or one tick) through Gnani's voice cloning
(`vachana-vc-v1`: the recording becomes a speaker embedding) and the voice preview that speaks a line with a saved voice."""

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import Request

try:
    from core import deps, engines, state
    from core.errors import AppError
    from core.retry import retry_async
    from core.validation import sanitize_text, sniff_audio_kind
    from services import (
        asr, consent_record as consent_records, gnani, provenance, speaking, storage, synthesis, voice_store,
    )
    from services.speaking import TtsPlan
except ImportError:  # pragma: no cover
    from backend.core import deps, engines, state
    from backend.core.errors import AppError
    from backend.core.retry import retry_async
    from backend.core.validation import sanitize_text, sniff_audio_kind
    from backend.services import (
        asr, consent_record as consent_records, gnani, provenance, speaking, storage, synthesis, voice_store,
    )
    from backend.services.speaking import TtsPlan

log = logging.getLogger("vakya")

CONSENT_MATCH_THRESHOLD = 0.6


# What a consent recording must say. The wizard shows "I consent to creating an AI version of my voice for the Vakya
# demonstration on <date>."; the server checks only the fixed part, so any date format or wording around it is fine.
CONSENT_STATEMENT = "I consent to creating an AI version of my voice"


def require_consent_statement(consent_text: str) -> None:
    """The text a recording is verified against must itself be a consent statement.

    The server compares the recording with text the CLIENT supplies, so without this the check passes for any
    audio paired with matching arbitrary text, and is skipped entirely when the text is simply left out."""
    words = lambda s: " ".join(re.findall(r"[a-z0-9]+", s.lower()))  # noqa: E731
    if not consent_text:
        raise AppError("CONSENT_REQUIRED", "The consent sentence that was read aloud must be sent with the recording.",
                       400, {"expected": CONSENT_STATEMENT})
    if words(CONSENT_STATEMENT) not in words(consent_text):
        raise AppError("CONSENT_INVALID", f"The consent sentence must say: \"{CONSENT_STATEMENT} ...\".", 422,
                       {"expected": CONSENT_STATEMENT})


def consent_match(challenge: str, heard: str) -> float:
    """Fraction of the challenge phrase's words that appear in the ASR transcript (0..1)."""
    norm = lambda s: re.findall(r"[a-z0-9]+", s.lower())  # noqa: E731
    want, got = norm(challenge), set(norm(heard))
    if not want:
        return 0.0
    return sum(1 for w in want if w in got) / len(want)


async def verify_consent(consent_bytes: bytes, challenge_text: str) -> dict:
    """Transcribe the spoken consent (Gnani Prisma) and compare it to the challenge phrase.
    Rejects a clear mismatch; when Gnani cannot hear it right now the recording is kept but marked unverified. With no
    Gnani key it raises "Needs a Gnani key" (the voice could not be cloned without one either)."""
    if not challenge_text:
        return {"verified": False, "score": None, "heard": None}
    try:
        heard = await asr.speech_to_text(consent_bytes, "consent", "en")
    except AppError as e:
        if getattr(e, "code", None) == "PROVIDER_NOT_CONFIGURED":
            raise
        log.warning("consent recording could not be checked (%s): kept unverified", e.code)
        return {"verified": False, "score": None, "heard": None}
    score = round(consent_match(challenge_text, heard), 2)
    if score < CONSENT_MATCH_THRESHOLD:
        raise AppError("CONSENT_MISMATCH",
                       "The consent recording does not match the challenge phrase. Please read it again clearly.",
                       422, {"heard": heard, "score": score})
    return {"verified": True, "score": score, "heard": heard}


async def auto_transcribe(wav_bytes: bytes, language: str) -> str:
    """Best-effort reference transcript (Gnani Prisma); empty string when unavailable."""
    try:
        text = await asr.speech_to_text(wav_bytes, "reference", language.split("-")[0] if language else None)
    except AppError as e:
        log.warning("reference auto-transcription unavailable (%s)", e.code)
        return ""
    if not text:
        return ""
    return sanitize_text(text, "transcript", required=False)


async def record_consent(user_id: str, storage_mode: str, consent_bytes: bytes, challenge_text: str,
                          verification: dict, challenge_id: Optional[str] = None) -> Optional[str]:
    """Persist the consent recording + challenge text. Returns the consent id (None if the DB write failed).

    Local mode keeps the recording on disk; the challenge id goes on the voice row (see
    clone_voice_v2). Cloud mode stores it on the `consents` row (migration 006)."""
    consent_id = str(uuid.uuid4())
    ext = sniff_audio_kind(consent_bytes) or "webm"
    if storage_mode == "local":
        path = os.path.join(state.LOCAL_AUDIO_DIR, f"consent_{consent_id}.{ext}")
        await asyncio.to_thread(lambda: open(path, "wb").write(consent_bytes))
        return consent_id
    audio_path = f"{user_id}/consents/{consent_id}.{ext}"
    row = {"id": consent_id, "user_id": user_id, "challenge_text": challenge_text, "audio_storage_path": audio_path}
    extras = {"challenge_id": challenge_id}
    try:
        await storage.sb_upload(audio_path, consent_bytes, f"audio/{ext}")
        try:
            await retry_async(lambda: state.supabase.table("consents").insert({**row, **extras}).execute(),
                              label="consents.insert")
        except Exception as e:  # noqa: BLE001
            if "challenge_id" not in str(e):
                raise
            # migration 006 has not been applied: keep the consent record without the new columns
            log.warning("consents table lacks challenge_id (apply 006_consent_speaker.sql)")
            await retry_async(lambda: state.supabase.table("consents").insert(row).execute(), label="consents.insert")
        return consent_id
    except Exception as e:  # noqa: BLE001
        log.warning("could not persist consent record (verified=%s): %s", verification.get("verified"), e)
        return None


async def record_tick_consent(user_id: str, storage_mode: str, consent_id: str, record: dict) -> Optional[str]:
    """Persist a one-tick consent (no recording). Returns the consent id (None if the cloud write failed).

    Local mode keeps the record on the voice row. Cloud mode uploads the record JSON to the usual consent-record
    path and inserts a `consents` row that points at it (audio_storage_path is NOT NULL, and deleting the voice
    removes whatever that path names), so the voices.consent_id foreign key and the withdrawal flow work unchanged."""
    if storage_mode == "local":
        return consent_id
    path = voice_store.consent_record_path(user_id, consent_id)
    try:
        await storage.sb_upload(path, json.dumps(record).encode("utf-8"), "application/json")
        await retry_async(lambda: state.supabase.table("consents").insert({
            "id": consent_id, "user_id": user_id, "challenge_text": consent_records.TICK_STATEMENT,
            "audio_storage_path": path}).execute(), label="consents.insert")
        return consent_id
    except Exception as e:  # noqa: BLE001
        log.warning("could not persist tick consent record: %s", e)
        return None


async def clone_voice_core(request: Request, user_id: str, file_content: bytes, consent_bytes: bytes,
                            consent_text: str, challenge_id: str, language: str, display_name: str,
                            transcript: str, consented_by: Optional[str] = None) -> dict:
    tick = consent_bytes is None  # one-tick consent: no recording, no sentence check
    gnani.require_key()
    if tick:
        verification = consent_records.tick_verification()
    else:
        verification = await verify_consent(consent_bytes, consent_text)
    if challenge_id:
        verification["challenge_id"] = challenge_id
    engine_key = engines.route_engine(language, "premium", True)
    if sniff_audio_kind(file_content) != "wav":
        # Browser recordings (webm/ogg) and compressed uploads are stored as WAV (16 kHz) so Gnani can read them.
        file_content = synthesis.to_wav_bytes(*(await synthesis.load_audio_bytes(file_content, 16000)))

    # Gnani's voice-clone endpoint turns the (consent-verified) recording into a speaker embedding, stored with the voice.
    # A failure (credits, rate limit, a clip outside 5-30 s) is the error of the request: there is no other engine to clone with.
    timbre_embedding: List[float] = await asyncio.to_thread(gnani.client.clone_embedding, file_content)

    voice_id = str(uuid.uuid4())
    ext = ".wav"
    storage_mode = deps.storage_mode_of(request)
    clone_mode = "embedding"

    if tick:
        consent_id = str(uuid.uuid4())
        record = consent_records.build_record(
            voice_id=voice_id, voice_name=display_name, consent_id=consent_id, challenge_sentence=None,
            challenge_id=None, verification=verification, language=language, engine=engine_key, method="tick",
            consented_by=consented_by, sample_sha256=consent_records.sample_sha256(file_content))
        consent_id = await record_tick_consent(user_id, storage_mode, consent_id, record)
        record["consent_id"] = consent_id
    else:
        consent_id = await record_consent(user_id, storage_mode, consent_bytes, consent_text, verification,
                                           challenge_id or None)
        record = consent_records.build_record(
            voice_id=voice_id, voice_name=display_name, consent_id=consent_id, challenge_sentence=consent_text,
            challenge_id=challenge_id or None, verification=verification, language=language, engine=engine_key)

    if storage_mode == "local":
        def write_local():
            voice_store.voice_file_write(os.path.join(state.LOCAL_AUDIO_DIR, f"{voice_id}.wav"), voice_id, "wav", file_content)
        await asyncio.to_thread(write_local)
        voice_store.save_local_voice({
            "id": voice_id,
            "project_id": "local_project",
            "user_id": user_id,
            "display_name": display_name,
            "engine": engine_key,
            "language": language,
            "reference_audio_path": f"local:{voice_id}",
            "transcript": transcript or None,
            "consent_id": consent_id,
            "consent_verified": verification["verified"],
            "consent_challenge_id": challenge_id or None,
            **({"consent_method": "tick"} if tick else {}),
            "consent_record": record,
            "created_at": datetime.now().isoformat()
        })
        await speaking.timbre_embedding_save(voice_id, user_id, storage_mode, timbre_embedding)
        return {"id": voice_id, "engine": engine_key, "mode": clone_mode, "consent_id": consent_id,
                "consent": verification, "consent_record": record, "message": "Voice profile saved locally"}

    storage_path = f"{user_id}/{voice_id}{ext}"
    await storage.sb_upload(storage_path, file_content, "audio/wav")
    project_id = await storage.get_or_create_project(user_id)

    await retry_async(lambda: state.supabase.table("voices").insert({
        "id": voice_id,
        "project_id": project_id,
        "engine": engine_key,
        "language": language,
        "reference_audio_path": storage_path,
        "display_name": display_name,
        "consent_id": consent_id
    }).execute(), label="voices.insert")
    if consent_id and not tick:  # the itemised record goes next to the consent recording (no schema change needed)
        try:
            await storage.sb_upload(voice_store.consent_record_path(user_id, consent_id), json.dumps(record).encode("utf-8"),
                            "application/json")
        except Exception as e:  # noqa: BLE001
            log.warning("could not store the consent record for voice %s: %s", voice_id, e)
    try:   # best effort: without it the embedding is made again on first use
        await speaking.timbre_embedding_save(voice_id, user_id, storage_mode, timbre_embedding)
    except Exception as e:  # noqa: BLE001
        log.warning("could not store the Gnani embedding of voice %s: %s", voice_id, e)

    return {"id": voice_id, "engine": engine_key, "mode": clone_mode, "consent_id": consent_id,
            "consent": verification, "consent_record": record, "message": "Voice profile saved successfully"}


async def preview_speech(text: str, voice_id: str, language: str, mode: str, user_id: str, storage_mode: str) -> Tuple[bytes, Optional[str]]:
    """The WAV and receipt id of `text` spoken with the saved voice `voice_id` (or the preset voice when none), labelled and
    with a signed receipt. Nothing is stored. Raises "Needs a Gnani key" without a key."""
    gnani.require_key()
    clone_required = bool(voice_id)
    engine_key = engines.route_engine(language, mode, clone_required)
    embedding: Optional[List[float]] = None
    voice_name: Optional[str] = None
    if engine_key == gnani.TIMBRE_CLONE_ENGINE:
        engine_key, embedding, voice_name = await speaking.resolve_timbre_clone(language, mode, voice_id, user_id, storage_mode)
    wav, output_sr = await speaking.timbre_speak(TtsPlan(engine_key, language, clone_required, voice_id=voice_id or None,
                                                         embedding=embedding), text)
    wav_bytes = synthesis.to_wav_bytes(synthesis.to_numpy(wav), output_sr)
    used_voice = voice_id if (clone_required and engine_key == gnani.TIMBRE_CLONE_ENGINE) else None
    consent_id, consent_verified, _name = await voice_store.voice_consent(used_voice, storage_mode, user_id)
    wav_bytes, receipt = await asyncio.to_thread(
        lambda: provenance.apply_provenance(
            kind="generation", subject_id=str(uuid.uuid4()), wav_bytes=wav_bytes, text=text, engine=engine_key,
            voice_id=used_voice, voice_name=voice_name, consent_id=consent_id, consent_verified=consent_verified,
            user_id=user_id, cloud=(storage_mode == "cloud" and state.supabase is not None)))
    return wav_bytes, receipt.get("receipt_id")
