"""Audio for the callers: speaking a line, slowing it, degrading it to an 8 kHz G.711 phone line with noise, and saving it small.

The degradation is the one of bench/stress_report.py (the same band limit, noise mix and mu-law round trip, fixed seed), applied
in the order a phone call applies it: the room's noise is mixed in at the scenario's SNR first, then the line band-limits to
300-3400 Hz and carries it at 8 kHz as G.711 mu-law. The SNR reported is measured on the two signals as they sit in the mix."""
from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
for p in (str(BENCH), str(BENCH.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

SR = 16000


def _sr():
    import stress_report as sr  # noqa: PLC0415 (heavy: scipy, soundfile)
    return sr


def wav_bytes(x: np.ndarray, sr: int = SR) -> bytes:
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, np.clip(x, -1.0, 1.0), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def read_wav_bytes(data: bytes) -> Tuple[np.ndarray, int]:
    import soundfile as sf
    x, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x.astype(np.float32), int(sr)


def to_16k(x: np.ndarray, sr: int) -> np.ndarray:
    return _sr().resample(x, sr, SR)


def slow_down(x: np.ndarray, speed: float) -> np.ndarray:
    """Change the speaking rate without changing the pitch (speed 0.8 = 25% slower), with short pauses added at the ends."""
    if abs(speed - 1.0) < 0.02:
        return x
    import librosa
    y = librosa.effects.time_stretch(x.astype(np.float32), rate=float(speed))
    return y.astype(np.float32)


def with_lead(x: np.ndarray, lead: float = 0.25, tail: float = 0.35) -> np.ndarray:
    return np.concatenate([np.zeros(int(lead * SR), np.float32), x, np.zeros(int(tail * SR), np.float32)])


def degrade_line(x16: np.ndarray, channel: Dict[str, Any], seed_parts: Tuple[Any, ...], others: List[np.ndarray]
                 ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """The caller's clip as the phone line delivers it. `channel` = {"codec": "g711"|"tel8k", "snr_db": number|None}."""
    sr = _sr()
    info: Dict[str, Any] = {"codec": channel.get("codec", "g711"), "snr_db_target": channel.get("snr_db"), "snr_db_measured": None}
    x = x16
    snr = channel.get("snr_db")
    if snr is not None:
        rng = np.random.default_rng(sr.stable_seed("theatre", *seed_parts))
        mixed, _xs, _ns, ninfo = sr.add_noise(x, float(snr), others, rng)
        info["snr_db_measured"] = round(float(ninfo["snr_db"]), 1)
        x = mixed
    x = sr.deg_tel8k(x, SR)
    if info["codec"] == "g711":
        x = sr.deg_g711(x, SR)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 0.98:
        x = x * (0.98 / peak)
    return x.astype(np.float32), info


def encode_small(wav: bytes, out: Path, bitrate: str = "40k") -> Path:
    """The WAV as a small mono MP3 (the page plays it); the WAV itself when ffmpeg or the encoder is missing."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("ffmpeg"):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td, "in.wav")
            src.write_bytes(wav)
            proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-ac", "1",
                                   "-codec:a", "libmp3lame", "-b:a", bitrate, str(out)], capture_output=True, text=True)
            if proc.returncode == 0 and out.exists():
                return out
    out = out.with_suffix(".wav")
    out.write_bytes(wav)
    return out


def cache_key(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]
