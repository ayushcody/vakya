"""Speaking one piece of text with Gnani's Timbre: the plan of a turn (the engine, the language, a cloned voice's speaker
embedding) and how a saved voice's embedding is stored and made. There is no other voice: when Gnani cannot answer the error
carries the gateway's plan (a held line on a live call) and with no key it says "Needs a Gnani key"."""

import asyncio
import dataclasses
import json
import logging
import os
from typing import Any, List, Optional, Tuple

import numpy as np

try:
    from core import deps, state
    from core.errors import AppError
    from services import gnani, storage, voice_store
except ImportError:  # pragma: no cover
    from backend.core import deps, state
    from backend.core.errors import AppError
    from backend.services import gnani, storage, voice_store

log = logging.getLogger("vakya")


@dataclasses.dataclass
class TtsPlan:
    """Which engine speaks a turn and everything it needs (a cloned voice's embedding)."""
    engine_key: str
    tts_language: str
    clone_required: bool
    voice_id: Optional[str] = None
    embedding: Optional[List[float]] = None   # Timbre voice cloning: the saved voice's Gnani speaker embedding
    phone: bool = False                        # a phone call: Timbre is asked for 8 kHz PCM (what the phone line carries)

    @property
    def used_voice(self) -> Optional[str]:
        """Only a cloned voice that was actually used is named in the receipt."""
        return self.voice_id if (self.clone_required and self.engine_key == gnani.TIMBRE_CLONE_ENGINE) else None


async def timbre_speak(plan: TtsPlan, text: str) -> Tuple[np.ndarray, int]:
    """Speak with Gnani's Timbre (the saved voice's embedding when the plan has one). Preview and web audio is 24 kHz WAV, a
    phone call's 8 kHz PCM. Nothing else speaks: when Gnani cannot answer the AppError (with the gateway's plan in its
    details) goes up to the turn, which plays a held line on a live call."""
    return await asyncio.to_thread(gnani.synth, text, plan.tts_language, phone=plan.phone, speaker_embedding=plan.embedding)


async def timbre_embedding_load(voice_id: str, user_id: str, storage_mode: str,
                                 row: Optional[dict]) -> Optional[List[float]]:
    """The saved Gnani speaker embedding of a voice (sealed file `{voice}.timbre.json`, local first, then Supabase), or
    None when it has none or consent was withdrawn."""
    deps.check_id(voice_id, "voice_id")
    local_path = os.path.join(state.LOCAL_AUDIO_DIR, f"{voice_id}.timbre.json")
    raw: Optional[bytes] = None
    if os.path.isfile(local_path):
        raw = await asyncio.to_thread(voice_store.voice_file_read, local_path, voice_id, "timbre")
    elif state.supabase is not None and storage_mode != "local":
        ref = (row or {}).get("reference_audio_path") or ""
        owner = ref.split("/")[0] if "/" in ref and not ref.startswith("local:") else user_id
        try:
            raw = await storage.sb_download(f"{owner}/{voice_id}.timbre.json")
        except Exception as e:  # noqa: BLE001 - best effort: a voice made before Gnani has none
            log.info("no Gnani embedding in Supabase for voice %s: %s", voice_id, e)
    if not raw:
        return None
    try:
        emb = json.loads(raw.decode("utf-8")).get("speaker_embedding")
        return [float(x) for x in emb] if emb else None
    except (ValueError, TypeError, AttributeError):
        return None


async def timbre_embedding_save(voice_id: str, user_id: str, storage_mode: str, embedding: List[float]) -> None:
    """Store the embedding with the voice: a sealed local file (the voice vault protects it like the recording and it is
    shredded with the voice), the row marked `timbre_embedding`, and in cloud mode a best-effort Supabase copy."""
    data = json.dumps({"speaker_embedding": embedding, "dims": len(embedding)}).encode("utf-8")
    await asyncio.to_thread(voice_store.voice_file_write, os.path.join(state.LOCAL_AUDIO_DIR, f"{voice_id}.timbre.json"), voice_id,
                            "timbre", data)
    if any(v.get("id") == voice_id for v in voice_store.get_local_voices()):
        await asyncio.to_thread(voice_store.update_local_voice, voice_id, timbre_embedding=True,
                                timbre_embedding_dims=len(embedding))
    if storage_mode != "local" and state.supabase is not None:
        try:
            await storage.sb_upload(f"{user_id}/{voice_id}.timbre.json", data, "application/json")
        except Exception as e:  # noqa: BLE001
            log.warning("could not store the Gnani embedding of voice %s in Supabase: %s", voice_id, e)


async def _timbre_embedding_make(voice_id: str, user_id: str, storage_mode: str,
                                 row: Optional[dict]) -> Optional[List[float]]:
    """Embed a saved voice that has no embedding yet (made before Gnani cloning was on) from its stored recording, and keep
    it. None when the recording cannot be read or Gnani does not answer."""
    local_wav = os.path.join(state.LOCAL_AUDIO_DIR, f"{voice_id}.wav")
    try:
        if os.path.isfile(local_wav):
            audio = await asyncio.to_thread(voice_store.voice_file_read, local_wav, voice_id, "wav")
        else:
            ref = (row or {}).get("reference_audio_path") or ""
            if storage_mode == "local" or "/" not in ref or ref.startswith("local:"):
                return None
            audio = await storage.sb_download(ref)
        embedding = await asyncio.to_thread(gnani.client.clone_embedding, audio)
        await timbre_embedding_save(voice_id, user_id, storage_mode, embedding)
        return embedding
    except AppError as e:
        if e.code == "CONSENT_WITHDRAWN":
            raise
        log.warning("could not embed voice %s with Gnani: %s", voice_id, e.message)
    except Exception as e:  # noqa: BLE001
        log.warning("could not embed voice %s with Gnani: %s", voice_id, e)
    return None


async def resolve_timbre_clone(language: str, mode: str, voice_id: str, user_id: str,
                                storage_mode: str) -> Tuple[str, Optional[List[float]], Optional[str]]:
    """For a saved voice: ("timbre_clone", its embedding, the voice's name), the embedding made now when the voice has none.
    When none can be made (Gnani does not answer, the recording is gone) the engine is "timbre", the preset voice, and the
    embedding None: the caller speaks with the preset voice and says so in the log. Raises VOICE_NOT_FOUND for a voice that
    is not there, and CONSENT_WITHDRAWN for one whose consent was withdrawn."""
    row = await voice_store.lookup_voice(voice_id, storage_mode, user_id)
    if row is None:
        raise AppError("VOICE_NOT_FOUND", voice_store.VOICE_NOT_FOUND_MSG, 404, {"voice_id": voice_id})
    embedding = await timbre_embedding_load(voice_id, user_id, storage_mode, row)
    if embedding is None:
        embedding = await _timbre_embedding_make(voice_id, user_id, storage_mode, row)
    if embedding is not None:
        return gnani.TIMBRE_CLONE_ENGINE, embedding, row.get("display_name")
    return gnani.TIMBRE_ENGINE, None, row.get("display_name")


async def turn_synth(plan: TtsPlan, spoken_text: str) -> Tuple[np.ndarray, int]:
    """Speak `spoken_text` with the planned engine. Returns (audio, sample rate)."""
    if plan.engine_key not in gnani.API_ENGINES:
        raise AppError("UNSUPPORTED_ENGINE", "Unsupported engine route", 500)
    return await timbre_speak(plan, spoken_text)
