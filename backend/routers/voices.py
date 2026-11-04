"""Voices: the saved voices, cloning one with consent, the preview that speaks with it, withdrawing consent (deleting it), the
retention report, the itemised consent record and transcribing a recording."""

import asyncio
import io
import json
import logging
import os
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse

try:
    from core import deps, engines, state
    from core.deps import get_current_user
    from core.errors import AppError
    from core.ratelimit import limiter
    from core.retry import retry_async
    from core.validation import sanitize_text, validate_audio_upload
    from services import (
        asr, consent_record as consent_records, gnani, provenance, speaking, storage, synthesis, voice_clone, voice_store,
    )
    from services.auth import LOCAL_USER
    from routers import consent as consent_challenges
except ImportError:  # pragma: no cover
    from backend.core import deps, engines, state
    from backend.core.deps import get_current_user
    from backend.core.errors import AppError
    from backend.core.ratelimit import limiter
    from backend.core.retry import retry_async
    from backend.core.validation import sanitize_text, validate_audio_upload
    from backend.services import (
        asr, consent_record as consent_records, gnani, provenance, speaking, storage, synthesis, voice_clone,
        voice_store,
    )
    from backend.services.auth import LOCAL_USER
    from backend.routers import consent as consent_challenges

log = logging.getLogger("vakya")

router = APIRouter()


@router.post("/api/transcribe")
@limiter.limit("10/minute")
async def transcribe_audio(request: Request, audio_file: UploadFile = File(...),
                           language: Optional[str] = Form(None),
                           user_id: str = Depends(get_current_user)):
    """Transcribe audio with Gnani's Prisma. The language is detected automatically unless one is given."""
    audio_bytes = await validate_audio_upload(audio_file, allow_recording=True)
    lang = sanitize_text(language, "language", 16).split("-")[0] if language else None
    print(f"Transcribing audio: {audio_file.filename}")
    hints: dict = {}
    token = asr.qu_module().ASR_CTX.set(hints)
    try:
        text = await asr.speech_to_text(audio_bytes, "transcribe", lang)
    finally:
        asr.qu_module().ASR_CTX.reset(token)
    result = {"text": text, "language": hints.get("detected_language") or lang or "en", "model": gnani.stt_model()}
    print(f"Transcription ({result['language']}): '{result['text'][:50]}...'")
    return result


@router.get("/api/voices")
async def list_voices(request: Request, user_id: str = Depends(get_current_user)):
    """List all saved voices."""
    if deps.storage_mode_of(request) == "local":
        voices = [v for v in voice_store.get_local_voices() if voice_store.owns_local(v, user_id)]
        voices.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return voices

    projs = await retry_async(
        lambda: state.supabase.table("projects").select("id").eq("owner_user_id", user_id).execute(), label="projects.select")
    ids = [p["id"] for p in (projs.data or [])]
    if not ids:
        return []
    res = await retry_async(
        lambda: state.supabase.table("voices").select("*").in_("project_id", ids).order("created_at", desc=True).execute(),
        label="voices.list")
    return res.data


@router.post("/api/voices")
async def save_voice(
    request: Request,
    name: str = Form(...),
    transcript: str = Form(""),
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user)
):
    """Save a voice (audio + optional transcript) to Supabase storage, with its Gnani speaker embedding."""
    name = sanitize_text(name, "name", 200)
    transcript = sanitize_text(transcript, "transcript", required=False)
    file_content = await validate_audio_upload(file)
    sb = storage.require_supabase()

    voice_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename or "")[1].lower()
    storage_path = f"{user_id}/{voice_id}{ext}"

    print(f"Creating the Gnani speaker embedding for {name}...")
    gnani.require_key()
    if ext != ".wav":
        file_content = synthesis.to_wav_bytes(*(await synthesis.load_audio_bytes(file_content, 16000)))
        storage_path = f"{user_id}/{voice_id}.wav"
    embedding = await asyncio.to_thread(gnani.client.clone_embedding, file_content)

    await storage.sb_upload(storage_path, file_content, "audio/wav")
    project_id = await storage.get_or_create_project(user_id)
    await retry_async(lambda: sb.table("voices").insert({
        "id": voice_id,
        "project_id": project_id,
        "engine": engines.CLONE_ENGINE,
        "language": "en",
        "reference_audio_path": storage_path,
        "display_name": name
    }).execute(), label="voices.insert")
    await speaking.timbre_embedding_save(voice_id, user_id, "cloud", embedding)

    return {"id": voice_id, "message": "Voice profile saved successfully"}


@router.delete("/api/voices/{voice_id}")
async def delete_voice(voice_id: str, request: Request, user_id: str = Depends(get_current_user)):
    """Withdraw consent and delete a saved voice completely: the voice, its reference sample and embedding, its
    consent recording and consent record, and the link from any agent that spoke with it. Receipts that name the voice are not deleted
    (they are an audit trail): /verify shows them as made with a voice whose owner withdrew consent."""
    deps.check_id(voice_id, "voice_id")
    result = await voice_store.delete_voice_core(voice_id, user_id, deps.storage_mode_of(request))
    gone = await asyncio.to_thread(provenance.record_withdrawal, voice_id, "withdrawn")
    result["withdrawn_at"] = gone["withdrawn_at"] if gone else None
    return result


@router.get("/api/voices/retention")
async def voice_retention_report(request: Request, user_id: str = Depends(get_current_user)):
    """Voices of the signed-in user that are past their retention period (VOICE_RETENTION_DAYS, default 365).

    Report only: nothing is deleted here. `enforce` says whether automatic deletion is on (the default; off with
    VOICE_RETENTION_ENFORCE=off), in which case expired voices are deleted when the server starts and by
    POST /api/voices/retention/enforce."""
    voices = await _owned_voices(request, user_id)
    return {"retention_days": consent_records.retention_days(), "enforce": consent_records.enforce_enabled(),
            "expired": consent_records.expired_voices(voices)}


@router.post("/api/voices/retention/enforce")
async def voice_retention_enforce(request: Request, user_id: str = Depends(get_current_user)):
    """Delete the signed-in user's voices that are past their retention period. Refused (409) when automatic deletion
    was turned off with VOICE_RETENTION_ENFORCE=off (on by default since 22 Oct 2026: the consent record promises a
    retention period, so the code keeps it)."""
    if not consent_records.enforce_enabled():
        raise AppError("RETENTION_ENFORCE_OFF", "Automatic deletion is off. Set VOICE_RETENTION_ENFORCE=on to "
                       "delete voices after their retention period.", 409)
    voices = await _owned_voices(request, user_id)
    mode = deps.storage_mode_of(request)
    deleted = []
    for item in consent_records.expired_voices(voices):
        try:
            await voice_store.delete_voice_core(item["id"], user_id, mode)
            await asyncio.to_thread(provenance.record_withdrawal, item["id"], "retention_expired")
            deleted.append(item)
        except Exception as e:  # noqa: BLE001
            log.warning("retention: could not delete voice %s: %s", item.get("id"), e)
    return {"retention_days": consent_records.retention_days(), "deleted": deleted}


@router.get("/api/voices/{voice_id}/consent-record")
async def get_voice_consent_record(voice_id: str, request: Request, user_id: str = Depends(get_current_user)):
    """The itemised consent record of one of your voices (404 for anyone else's): purpose, what is stored, the
    sentence and code that were read, match scores, when, how long it is kept, and the notice version shown.

    Voices cloned before records existed return a partial record with `legacy: true`."""
    deps.check_id(voice_id, "voice_id")
    row = await voice_store.lookup_voice(voice_id, deps.storage_mode_of(request), user_id)
    if not row:
        raise AppError("VOICE_NOT_FOUND", "Voice not found", 404)
    is_cloud_row = not str(row.get("reference_audio_path") or "local:").startswith("local:")
    if is_cloud_row and voice_store.UUID_RE.match(str(row.get("consent_id") or "")) and not isinstance(row.get("consent_record"), dict):
        owner = str(row["reference_audio_path"]).split("/")[0]
        try:
            blob = await storage.sb_download(voice_store.consent_record_path(owner, str(row["consent_id"])))
            stored = json.loads(blob)
            if isinstance(stored, dict):
                row = {**row, "consent_record": stored}
        except Exception as e:  # noqa: BLE001 - voice cloned before records existed, or storage unavailable
            log.info("no stored consent record for voice %s: %s", voice_id, e)
    return consent_records.record_for_row(row)


async def _owned_voices(request: Request, user_id: str) -> List[dict]:
    """The signed-in user's voice rows (local json or Supabase), for the retention report."""
    if deps.storage_mode_of(request) == "local":
        return [v for v in voice_store.get_local_voices() if voice_store.owns_local(v, user_id)]
    return await list_voices(request, user_id)


async def retention_startup():
    """Local voices past VOICE_RETENTION_DAYS: always logged; deleted unless VOICE_RETENTION_ENFORCE=off."""
    try:
        expired = consent_records.expired_voices(voice_store.get_local_voices())
        if not expired:
            return
        names = ", ".join(f"{e['name']} (due {str(e['retain_until'])[:10]})" for e in expired)
        if not consent_records.enforce_enabled():
            log.warning("%d voice(s) are past their %d-day retention period (not deleted: set "
                        "VOICE_RETENTION_ENFORCE=on to delete them): %s", len(expired),
                        consent_records.retention_days(), names)
            return
        voices = {v.get("id"): v for v in voice_store.get_local_voices()}
        for item in expired:
            owner = voices.get(item["id"], {}).get("user_id") or LOCAL_USER
            try:
                await voice_store.delete_voice_core(item["id"], owner, "local")
                await asyncio.to_thread(provenance.record_withdrawal, item["id"], "retention_expired")
                log.info("retention: deleted voice %s (%s)", item["id"], item["name"])
            except Exception as e:  # noqa: BLE001
                log.warning("retention: could not delete voice %s: %s", item["id"], e)
    except Exception as e:  # noqa: BLE001 - never stop the server from starting
        log.warning("voice retention check failed: %s", e)


@router.post("/api/voices/clone")
@limiter.limit("5/minute")
async def clone_voice_v2(
    request: Request,
    reference_audio: UploadFile = File(...),
    consent_audio: UploadFile = File(None),
    consent_text: str = Form(""),
    consent_confirmed: str = Form("false"),
    challenge_id: str = Form(""),
    language: str = Form("en"),
    display_name: str = Form(...),
    transcript: str = Form(""),
    consent_given: str = Form(""),
    consent_name: str = Form(""),
    user_id: str = Depends(get_current_user)
):
    """
    Clone a voice. CONSENT_MODE=tick (the default) needs only `consent_given=true` (and optionally `consent_name`):
    the one-tick consent is stored as a record with method "tick". CONSENT_MODE=strict is the flow below.

    Strict mode: requires a spoken consent recording, verified with Gnani's Prisma (the words read must match).

    The recording should read a fresh sentence from `GET /api/consent/challenge`: pass its `challenge_id` and the
    server verifies against the stored text (an expired, unknown or used id is 422 CONSENT_CHALLENGE_INVALID).
    Without `challenge_id` the client's `consent_text` is used, which must say "I consent to creating an AI version
    of my voice". The voice is cloned by Gnani (`vachana-vc-v1`): the recording becomes a speaker embedding that is saved with
    the voice. Needs a Gnani key.
    """
    display_name = sanitize_text(display_name, "display_name", 200)
    transcript = sanitize_text(transcript, "transcript", required=False)
    consent_text = sanitize_text(consent_text, "consent_text", 500, required=False)
    challenge_id = (challenge_id or "").strip()
    language = deps.check_language(language)
    if consent_records.consent_mode() == "tick":
        if not consent_records.is_ticked(consent_given):
            raise AppError("CONSENT_REQUIRED", "Tick the box to confirm you own this voice or have the speaker's "
                           "permission to clone it.", 400, {"field": "consent_given"})
        consent_name = sanitize_text(consent_name, "consent_name", 120, required=False)
        file_content = await validate_audio_upload(reference_audio, allow_recording=True, field="reference_audio")
        return await voice_clone.clone_voice_core(request, user_id, file_content, None, consent_records.TICK_STATEMENT, "",
                                       language, display_name, transcript, consented_by=consent_name)
    if consent_confirmed.lower() != "true" or consent_audio is None or not consent_audio.filename:
        raise AppError("CONSENT_REQUIRED", "A spoken consent recording is required before cloning a voice.", 400)
    if challenge_id:
        consent_text = consent_challenges.claim(challenge_id, user_id)  # the stored text; the client's is ignored
    else:
        voice_clone.require_consent_statement(consent_text)
    done = False
    try:
        consent_bytes = await validate_audio_upload(consent_audio, allow_recording=True, field="consent_audio")
        file_content = await validate_audio_upload(reference_audio, allow_recording=True, field="reference_audio")
        result = await voice_clone.clone_voice_core(request, user_id, file_content, consent_bytes, consent_text, challenge_id,
                                         language, display_name, transcript)
        done = True
        return result
    finally:
        if challenge_id:
            consent_challenges.finish(challenge_id, done)


@router.post("/api/voices/generate")
@limiter.limit("10/minute")
async def generate_voice_v2(
    request: Request,
    text: str = Form(...),
    voice_id: str = Form(""),
    language: str = Form("en"),
    mode: str = Form("fast"),
    user_id: str = Depends(get_current_user)
):
    """The preview of a voice: `text` spoken with the saved voice `voice_id` (the preset voice when empty). Returns the
    WAV, labelled and signed, with the receipt id (X-Receipt-Id). Nothing is stored."""
    text = sanitize_text(text, "text")
    language = deps.check_language(language)
    mode = sanitize_text(mode, "mode", 32)
    if voice_id:
        deps.check_id(voice_id, "voice_id")
    try:
        wav_bytes, receipt_id = await voice_clone.preview_speech(text, voice_id, language, mode, user_id, deps.storage_mode_of(request))
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001  surface the real reason instead of a bare 500
        log.exception("voice preview failed")
        raise AppError("GENERATION_FAILED", f"Speech generation failed: {type(e).__name__}: {str(e)[:200]}", 500) from e
    headers = {"Content-Disposition": "attachment; filename=preview.wav"}
    if receipt_id:
        headers["X-Receipt-Id"] = str(receipt_id)
    return StreamingResponse(io.BytesIO(wav_bytes), media_type="audio/wav", headers=headers)
