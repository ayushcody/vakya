"""Audio helpers that need no storage: audio arrays and WAV bytes, decoding an upload, splitting long text and resampling.
The speech itself comes from Gnani's Timbre (services/gnani.py)."""

import asyncio
import io
import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import librosa
except Exception:  # noqa: BLE001
    librosa = None

try:
    import soundfile as sf
except Exception:  # noqa: BLE001
    sf = None

try:
    from core.errors import AppError
    from core.validation import sniff_audio_kind
except ImportError:  # pragma: no cover
    from backend.core.errors import AppError
    from backend.core.validation import sniff_audio_kind

log = logging.getLogger("vakya")


def to_numpy(wav: Any) -> np.ndarray:
    return np.asarray(wav, dtype=np.float32).reshape(-1)


def to_wav_bytes(wav: Any, sr: int) -> bytes:
    if sf is None:
        raise AppError("MODEL_UNAVAILABLE", "soundfile is not installed", 503)
    buf = io.BytesIO()
    sf.write(buf, to_numpy(wav), int(sr), format='WAV')
    return buf.getvalue()


async def load_audio_bytes(audio_bytes: bytes, sample_rate: int = 16000) -> Tuple[np.ndarray, int]:
    """Decode uploaded audio bytes into mono float32 (16 kHz unless asked otherwise; via temp file for ffmpeg formats)."""
    if librosa is None:
        raise AppError("MODEL_UNAVAILABLE", "librosa is not installed", 503)
    suffix = "." + (sniff_audio_kind(audio_bytes) or "wav")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        from services.audio_decode import decode_file
    except ImportError:  # pragma: no cover
        from backend.services.audio_decode import decode_file
    try:
        audio = await asyncio.to_thread(decode_file, tmp_path, sample_rate)
        return audio, sample_rate
    except Exception as e:  # noqa: BLE001
        raise AppError("AUDIO_DECODE_FAILED", "Could not decode the uploaded audio", 422) from e
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def smart_split_text(text: str, max_chars: int = 500) -> List[str]:
    """
    Splits long text into smaller chunks based on sentence boundaries.
    Prioritizes splitting at punctuation to preserve flow.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    current_chunk = ""

    # Split by sentence endings using regex lookbehind
    sentences = re.split(r'(?<=[.!?])\s+', text)

    for sentence in sentences:
        if len(current_chunk) + len(sentence) < max_chars:
            current_chunk += sentence + " "
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())

            # Reset current chunk
            current_chunk = sentence + " "

            # Handle extremely long single sentences by splitting at commas or spaces
            while len(current_chunk) > max_chars:
                split_point = current_chunk.rfind(',', 0, max_chars)
                if split_point == -1:
                    split_point = current_chunk.rfind(' ', 0, max_chars)

                if split_point != -1:
                    chunks.append(current_chunk[:split_point+1].strip())
                    current_chunk = current_chunk[split_point+1:]
                else:
                    # Hard split if absolutely no break points found
                    chunks.append(current_chunk[:max_chars].strip())
                    current_chunk = current_chunk[max_chars:]

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


def resample_linear(x: np.ndarray, orig: int, new: int) -> np.ndarray:
    if orig == new or x.size == 0:
        return x
    n = max(1, int(round(x.size * new / orig)))
    return np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x).astype(np.float32)
