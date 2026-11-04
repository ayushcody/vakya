"""Speech to text: Gnani Prisma, one utterance at a time over REST (the realtime socket is used by the phone path, see
services/gnani_client.StreamingRecognizer). There is no other recogniser: when Gnani cannot answer the error carries the
gateway's plan (`details.degraded`, `action`, `hold_line`) and the live call plays its held line; with no GNANI_API_KEY
the error says "Needs a Gnani key"."""

import asyncio
import logging
from typing import Optional

try:
    from core.validation import sniff_audio_kind
    from services import gnani, hearing, synthesis
except ImportError:  # pragma: no cover
    from backend.core.validation import sniff_audio_kind
    from backend.services import gnani, hearing, synthesis

log = logging.getLogger("vakya")


def qu_module():
    """services/query_understanding.py (imported on first use)."""
    try:
        from services import query_understanding
    except ImportError:  # pragma: no cover
        from backend.services import query_understanding
    return query_understanding


_GNANI_AUDIO_TYPES = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg", "flac": "audio/flac"}


async def _gnani_speech_to_text(audio_bytes: bytes, kind: str, language: Optional[str], hints: Optional[dict]) -> str:
    """One utterance through Gnani's Prisma (REST, format=codemix by default; see services/gnani.py). The agent's languages
    (at most four, `indic-auto` when unknown), its keyterms and the caller's previous words come from the turn's hints.
    Browser recordings (webm) are re-encoded to WAV first. Sets hints["detected_language"], the language Gnani heard (the
    app's two-letter code), so the language policy keeps its input. Raises AppError (PROVIDER_CREDITS, RATE_LIMITED, ...)."""
    hints = hints if hints is not None else {}
    if kind in _GNANI_AUDIO_TYPES:
        payload, ctype, ext = audio_bytes, _GNANI_AUDIO_TYPES[kind], kind
    else:
        payload, ctype, ext = synthesis.to_wav_bytes(*(await synthesis.load_audio_bytes(audio_bytes, 16000))), "audio/wav", "wav"
    languages = hints.get("asr_languages") or ([language] if language else None)
    out = await asyncio.to_thread(
        lambda: gnani.transcribe(payload, filename=f"audio.{ext}", content_type=ctype, languages=languages,
                                 keyterms=hints.get("keyterms"), previous_text=hints.get("previous_text"),
                                 topic=hints.get("topic"), speaker_names=hints.get("speaker_names"),
                                 substitution_map=hints.get("substitution_map"), request_id=hints.get("request_id")))
    hints["detected_language"] = out.get("language")
    hints["detected_language_code"] = out.get("detected_language_code")   # Gnani's own code ("hi-IN"), as it came back
    hints["request_id"] = out.get("request_id") or hints.get("request_id")  # the X-API-Request-ID Gnani echoed
    hints["processing_time"] = out.get("processing_time")                    # seconds Gnani spent, upload excluded
    hints["prompt_used"] = False
    hints["segments"] = None   # Prisma gives no per-segment confidence: the turn's confidence is "unknown"
    hints["provider"] = "gnani"
    hearing.after_transcript(hints, out["text"])   # which learnt corrections probably ran (an event each)
    return out["text"]


async def speech_to_text(audio_bytes: bytes, name: str, language: Optional[str] = None) -> str:
    """Speech to text with Gnani's Prisma. Raises AppError: "Needs a Gnani key" (PROVIDER_NOT_CONFIGURED, 503) without a key,
    and for a failure at Gnani (credits, rate limit, a clip over 30 s, unreachable) the same error the gateway made, with its
    plan in `details` (hold_line, action) so a live call can say its held line instead of going silent.

    A live turn sets query_understanding.ASR_CTX around this call: the agent's languages, its keyterms and the caller's
    previous words go to Prisma as hints, and Gnani's detected language is put back into that dict. Other callers (consent,
    voice notes) see no change."""
    gnani.require_key()
    kind = sniff_audio_kind(audio_bytes) or "webm"
    hints = qu_module().asr_hints()
    return await _gnani_speech_to_text(audio_bytes, kind, language, hints)
