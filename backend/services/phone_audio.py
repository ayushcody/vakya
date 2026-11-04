"""Audio plumbing for phone calls over Vobiz's bidirectional stream (routers/phone.py runs the call).

Nothing here knows about the web app. It is the part that can be tested with plain numbers:

  * PCM16 helpers and a proper resampler (soxr, else scipy's polyphase filter, else ffmpeg), plus G.711 mu-law in
    case a stream is ever negotiated as mu-law;
  * chunking: audio goes out in chunks that are a multiple of 320 bytes (20 ms at 8 kHz PCM16), at least 3.2 KB and at
    most 100 KB (a size the carrier we first built this for required; Vobiz accepts any size, so it only paces well);
  * `Pacer`: sends audio just ahead of real time (a small lead, never a flood), so a `clear` for barge-in only has to
    drop a fraction of a second that Vobiz has queued;
  * voice activity detection on the caller's side: `EnergyVad`, a loudness gate with hysteresis on the 8 kHz stream
    (a frame is speech when its RMS is clearly above the line's noise floor; it stays speech until it falls well below
    that), and `UtteranceDetector`, the small state machine that turns a stream of frames into "the caller started
    talking over us" (barge-in, which also needs a minimum loudness) and "the caller finished a sentence" events, plus (with
    `early_silence_ms`) "the caller has probably finished" and "no, they went on", which the call uses to start the reply
    speculatively before the end of the sentence is certain. The end of a turn is silence timing (services/turn_taking.py
    picks how long) and the transcript's own completeness; nothing else listens to the line.

The detector is driven by the audio's own clock (frames), never the wall clock, so it behaves the same for a real
call and for a test that feeds a recording faster than real time.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np

log = logging.getLogger("vakya.phone.audio")

SLIN_FRAME_BYTES = 320            # 20 ms of 8 kHz 16-bit mono: chunks are a multiple of this
MIN_CHUNK_BYTES = 3200            # the minimum chunk
MAX_CHUNK_BYTES = 100 * 1024      # ...and maximum
DEFAULT_CHUNK_BYTES = 3200
PHONE_RATE = 8000
SUPPORTED_RATES = (8000, 16000, 24000)  # the Voicebot applet's `sample-rate` options
ASR_RATE = 16000


# ------------------------------------------------------------------------------------------------------- PCM

def pcm16_to_float(pcm: bytes) -> np.ndarray:
    """Little-endian 16-bit mono PCM -> float32 in [-1, 1]. A trailing odd byte is dropped."""
    n = len(pcm) // 2
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(pcm, dtype="<i2", count=n).astype(np.float32) / 32768.0


def float_to_pcm16(x: np.ndarray) -> bytes:
    x = np.asarray(x, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return (np.clip(x, -1.0, 1.0) * 32767.0).round().astype("<i2").tobytes()


# G.711 mu-law (only used when a stream's start event says the media is mu-law)
_ULAW_BIAS = 0x84
_ULAW_CLIP = 32635


def ulaw_decode(data: bytes) -> bytes:
    u = ~np.frombuffer(data, dtype=np.uint8)
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = (((mantissa.astype(np.int32) << 3) + _ULAW_BIAS) << exponent) - _ULAW_BIAS
    sample = np.where(sign != 0, -sample, sample)
    return sample.astype("<i2").tobytes()


def ulaw_encode(pcm: bytes) -> bytes:
    s = np.frombuffer(pcm, dtype="<i2", count=len(pcm) // 2).astype(np.int32)
    sign = (s < 0).astype(np.int32) << 7
    mag = np.minimum(np.abs(s), _ULAW_CLIP) + _ULAW_BIAS
    exponent = np.zeros_like(mag)
    for e in range(7, 0, -1):  # the position of the highest set bit above bit 7
        exponent = np.where((exponent == 0) & (mag >= (1 << (e + 7))), e, exponent)
    mantissa = (mag >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)).astype(np.uint8).tobytes()


# --------------------------------------------------------------------------------------------------- resample

def _resample_soxr(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    import soxr  # noqa: PLC0415
    return soxr.resample(x, src, dst, quality="HQ")


def _resample_scipy(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    from scipy.signal import resample_poly  # noqa: PLC0415
    g = math.gcd(int(src), int(dst))
    return resample_poly(x, int(dst) // g, int(src) // g).astype(np.float32)


def _resample_ffmpeg(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("no resampler available: install soxr or scipy, or ffmpeg")
    out = subprocess.run([exe, "-v", "error", "-f", "s16le", "-ar", str(src), "-ac", "1", "-i", "pipe:0",
                          "-f", "s16le", "-ar", str(dst), "-ac", "1", "pipe:1"], input=float_to_pcm16(x),
                         capture_output=True, check=True, timeout=30).stdout
    return pcm16_to_float(out)


def resampler_name() -> str:
    for name, mod in (("soxr", "soxr"), ("scipy", "scipy.signal")):
        try:
            __import__(mod)
            return name
        except ImportError:
            continue
    return "ffmpeg"


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Float audio from `src` to `dst` Hz with an anti-aliasing filter (soxr HQ, else scipy polyphase, else ffmpeg).
    Not the linear interpolation the web pipeline uses for gluing clips: that one aliases badly when going 24k -> 8k."""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if src == dst or x.size == 0:
        return x
    for fn in (_resample_soxr, _resample_scipy, _resample_ffmpeg):
        try:
            return np.asarray(fn(x, src, dst), dtype=np.float32)
        except ImportError:
            continue
    raise RuntimeError("no resampler available")  # pragma: no cover - _resample_ffmpeg raises its own


def to_phone_pcm(x: np.ndarray, src: int, dst: int = PHONE_RATE) -> bytes:
    """Synthesized float audio at `src` Hz -> the PCM16 bytes the phone line plays (at `dst` Hz)."""
    return float_to_pcm16(resample(x, src, dst))


def to_asr_wav(pcm: bytes, src: int) -> bytes:
    """A caller's PCM16 utterance at `src` Hz -> a 16 kHz mono WAV for speech recognition."""
    import io
    import wave
    x = resample(pcm16_to_float(pcm), src, ASR_RATE)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(ASR_RATE)
        w.writeframes(float_to_pcm16(x))
    return buf.getvalue()


# ----------------------------------------------------------------------------------------------------- chunks

def clean_chunk_bytes(n: int) -> int:
    """A chunk size the pacer uses: a multiple of 320 bytes between 3.2 KB and 100 KB."""
    n = max(MIN_CHUNK_BYTES, min(MAX_CHUNK_BYTES, int(n)))
    return n - n % SLIN_FRAME_BYTES


def chunk_pcm(pcm: bytes, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> List[bytes]:
    """Cut PCM16 into chunks of `chunk_bytes`. Every chunk, the last one included, is a multiple of 320 bytes and at
    least 3.2 KB, the chunk range: the last chunk is padded with silence (at most 100 ms at
    the end of a sentence), because a short final packet can leave a gap."""
    size = clean_chunk_bytes(chunk_bytes)
    if len(pcm) % 2:
        pcm = pcm[:-1]
    out = [pcm[i:i + size] for i in range(0, len(pcm), size)]
    if out and len(out[-1]) < MIN_CHUNK_BYTES:
        out[-1] = out[-1] + b"\x00" * (MIN_CHUNK_BYTES - len(out[-1]))
    elif out and len(out[-1]) % SLIN_FRAME_BYTES:
        out[-1] = out[-1] + b"\x00" * (SLIN_FRAME_BYTES - len(out[-1]) % SLIN_FRAME_BYTES)
    return out


def pcm_seconds(nbytes: int, rate: int) -> float:
    return nbytes / 2.0 / rate


# ------------------------------------------------------------------------------------------------------ pacing

class Pacer:
    """Keeps the audio Vobiz has queued to `lead_s` seconds: sending faster than real time is what makes a `clear`
    useless (everything is already queued), sending slower makes the caller hear gaps. `speed` > 1 runs the clock
    faster (tests); 0 disables the waiting."""

    def __init__(self, lead_s: float = 0.4, speed: float = 1.0, clock: Callable[[], float] = time.monotonic):
        self.lead_s = max(0.0, lead_s)
        self.speed = speed
        self.clock = clock
        self.play_end = 0.0     # when the audio sent so far finishes playing (on `clock`)
        self.total_sent_s = 0.0

    def reset(self) -> None:
        """Nothing queued any more (after a clear, or when the call starts)."""
        self.play_end = 0.0

    @property
    def buffered_s(self) -> float:
        return max(0.0, self.play_end - self.clock())

    @property
    def playing(self) -> bool:
        return self.buffered_s > 0.0

    def wait_needed(self) -> float:
        """Seconds to wait before the next chunk may be sent."""
        if self.speed <= 0:
            return 0.0
        return max(0.0, self.buffered_s - self.lead_s)

    async def wait(self) -> None:
        delay = self.wait_needed()
        if delay > 0:
            await asyncio.sleep(delay)

    def sent(self, seconds: float) -> None:
        now = self.clock()
        if self.speed <= 0:
            self.total_sent_s += seconds
            return
        self.play_end = max(self.play_end, now) + seconds / self.speed
        self.total_sent_s += seconds

    def finished(self) -> None:
        """Vobiz said it played the last checkpoint (playedStream)."""
        self.play_end = min(self.play_end, self.clock())


# --------------------------------------------------------------------------------------------------------- VAD

def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


class EnergyVad:
    """A frame is speech when it is clearly louder than the background the line has had so far, with hysteresis: it enters
    speech above `enter` (the larger of PHONE_VAD_ENTER_RMS, default 0.012, and PHONE_VAD_NOISE_X, default 3, times the
    noise floor) and stays speech until it falls under `leave` (a fraction of `enter`, PHONE_VAD_LEAVE_RATIO, default
    0.55), so the quiet ends of words are not cut. The noise floor (RMS) is learnt from the quiet frames; a level that
    stays loud for six seconds is taken as steady noise and becomes the floor.

    `probability` is 1.0 above `enter`, 0.4 between `leave` and `enter` (the detector keeps the previous state there), 0.0
    below `leave`."""

    name = "energy"
    STUCK_S = 6.0

    def __init__(self, rate: int):
        self.rate = rate
        self.window = 256 if rate == 8000 else 512
        self.enter_min = _float_env("PHONE_VAD_ENTER_RMS", 0.012)
        self.noise_x = _float_env("PHONE_VAD_NOISE_X", 3.0)
        self.leave_ratio = min(0.95, max(0.1, _float_env("PHONE_VAD_LEAVE_RATIO", 0.55)))
        self.reset()

    def reset(self) -> None:
        self.floor = 0.004   # the noise floor (RMS), adapted from quiet frames
        self.active = False
        self._loud_frames = 0

    @property
    def enter(self) -> float:
        return max(self.enter_min, self.floor * self.noise_x)

    @property
    def leave(self) -> float:
        return self.enter * self.leave_ratio

    def probability(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(np.square(frame)))) if frame.size else 0.0
        enter, leave = self.enter, self.leave
        if rms > enter:
            level = 1.0
        elif rms > leave and self.active:
            level = 0.4
        else:
            level = 0.0
        self.active = level > 0.0
        if level == 0.0:
            self.floor = 0.95 * self.floor + 0.05 * max(rms, 0.0005)
            self._loud_frames = 0
        else:
            self._loud_frames += 1
            if self._loud_frames * self.window / self.rate > self.STUCK_S:   # steady noise, not speech
                self.floor = max(self.floor, 0.5 * rms)
                self._loud_frames = 0
        return level


def make_vad(rate: int):
    """The voice activity detector for a stream at `rate` Hz (8000 or 16000): the energy gate."""
    return EnergyVad(rate)


# ------------------------------------------------------------------------------------------- utterance detector

@dataclass
class VadEvent:
    kind: str                 # "barge": speech long enough to cut in | "utterance": a finished sentence | "noise"
    #                           "pause": early_silence_ms of silence after real speech (probably finished; the audio so
    #                           far comes with it) | "resume": speech again after a pause (the pause was not the end)
    audio: bytes = b""        # PCM16 at the caller's rate (utterance and pause only)
    speech_ms: int = 0
    at_ms: int = 0            # position in the caller's audio when this happened


@dataclass
class DetectorConfig:
    end_silence_ms: int = 500
    early_silence_ms: int = 0     # > 0 (and below end_silence_ms): emit "pause" after this much silence, then "resume"
    min_speech_ms: int = 400
    barge_ms: int = 300
    start_ms: int = 96            # speech needed before an utterance counts as started
    preroll_ms: int = 256         # audio kept from before the start so the first syllable is not clipped
    max_utterance_ms: int = 20000
    threshold: float = 0.5
    neg_threshold: float = 0.35   # a frame below this is silence; in between keeps the previous state
    barge_rms: float = 0.0        # barge-in also needs the speech so far to average at least this RMS (PHONE_BARGE_RMS)


class UtteranceDetector:
    """Frames in, events out. See the module docstring. `feed` takes PCM16 at the detector's rate in any chunk size."""

    def __init__(self, rate: int = PHONE_RATE, vad=None, config: Optional[DetectorConfig] = None):
        self.cfg = config or DetectorConfig()
        self.vad_rate = rate if rate in (8000, 16000) else 16000
        self.in_rate = rate
        self.vad = vad or make_vad(self.vad_rate)
        self.window = self.vad.window
        self.frame_ms = int(round(1000.0 * self.window / self.vad_rate))
        self._rest = np.zeros(0, dtype=np.float32)
        self._preroll: List[bytes] = []
        self.reset()

    def reset(self) -> None:
        self.vad.reset()
        self._rest = np.zeros(0, dtype=np.float32)
        self._preroll = []
        self._clear_utterance()
        self.pos_ms = 0
        self.speech_run = 0
        self._last_speech = False

    def _clear_utterance(self) -> None:
        self.in_utterance = False
        self._frames: List[bytes] = []
        self._voiced = 0          # voiced frames in this utterance
        self._voiced_rms = 0.0    # the sum of those frames' RMS (barge-in needs a minimum average)
        self._run_rms: List[float] = []   # RMS of the speech frames in a row before the utterance starts
        self._silence = 0         # trailing silent frames
        self._barged = False
        self._paused = False      # a "pause" was emitted for the current run of silence
        self.speech_run = 0

    @property
    def speaking(self) -> bool:
        return self.in_utterance

    @property
    def end_silence_ms(self) -> int:
        return self.cfg.end_silence_ms

    def set_end_silence(self, ms: int) -> None:
        """The silence that ends the caller's next utterance (and the one in progress: the check runs on every frame).
        The call sets it after each agent reply from what the reply asked for (services/turn_taking.py): a caller
        asked for a phone number pauses between its groups. The early "pause" point stays where it was and is only
        used while it is below the end."""
        self.cfg.end_silence_ms = max(int(ms), self.frame_ms)

    def feed(self, pcm: bytes) -> List[VadEvent]:
        x = pcm16_to_float(pcm)
        if self.in_rate not in (8000, 16000):
            x = resample(x, self.in_rate, self.vad_rate)
        x = np.concatenate([self._rest, x]) if self._rest.size else x
        events: List[VadEvent] = []
        n = len(x) // self.window
        for i in range(n):
            frame = x[i * self.window:(i + 1) * self.window]
            events.extend(self._frame(frame))
        self._rest = x[n * self.window:]
        return events

    def _frame(self, frame: np.ndarray) -> List[VadEvent]:
        cfg = self.cfg
        prob = self.vad.probability(frame)
        speech = prob >= cfg.threshold or (self._last_speech and prob >= cfg.neg_threshold)
        self._last_speech = speech
        raw = float_to_pcm16(frame)
        rms = float(np.sqrt(np.mean(np.square(frame)))) if frame.size else 0.0
        self.pos_ms += self.frame_ms
        events: List[VadEvent] = []

        if not self.in_utterance:
            self.speech_run = self.speech_run + 1 if speech else 0
            self._run_rms = (self._run_rms + [rms]) if speech else []
            self._preroll.append(raw)
            keep = max(1, cfg.preroll_ms // self.frame_ms)
            if len(self._preroll) > keep + cfg.start_ms // self.frame_ms:
                self._preroll.pop(0)
            if self.speech_run * self.frame_ms >= cfg.start_ms:
                self.in_utterance = True
                self._frames = list(self._preroll)
                self._voiced = self.speech_run
                self._voiced_rms = float(sum(self._run_rms[-self.speech_run:]))
                self._run_rms = []
                self._silence = 0
                self._preroll = []
            barge = self._maybe_barge()
            return [barge] if barge else []

        self._frames.append(raw)
        if speech:
            self._voiced += 1
            self._voiced_rms += rms
            self._silence = 0
            self.speech_run += 1
            if self._paused:  # the caller went on after a pause: whatever was started on it is void
                self._paused = False
                events.append(VadEvent("resume", speech_ms=self._voiced * self.frame_ms, at_ms=self.pos_ms))
        else:
            self._silence += 1
            self.speech_run = 0
        barge = self._maybe_barge()
        if barge:
            events.append(barge)
        total_ms = len(self._frames) * self.frame_ms
        if self._silence * self.frame_ms >= cfg.end_silence_ms or total_ms >= cfg.max_utterance_ms:
            events.append(self._finish())
            return events
        early = cfg.early_silence_ms
        if (0 < early < cfg.end_silence_ms and not self._paused and self._silence * self.frame_ms >= early
                and self._voiced * self.frame_ms >= cfg.min_speech_ms):
            # Probably the end of the sentence: the same audio the utterance event would carry if it ended now.
            self._paused = True
            events.append(VadEvent("pause", audio=self._audio_so_far(), speech_ms=self._voiced * self.frame_ms,
                                   at_ms=self.pos_ms))
        return events

    def _maybe_barge(self) -> Optional[VadEvent]:
        if (self.in_utterance and not self._barged and self._voiced * self.frame_ms >= self.cfg.barge_ms
                and self._voiced_rms / max(1, self._voiced) >= self.cfg.barge_rms):
            self._barged = True
            return VadEvent("barge", speech_ms=self._voiced * self.frame_ms, at_ms=self.pos_ms)
        return None

    def _audio_so_far(self) -> bytes:
        """The utterance's audio up to its last speech plus a short tail of the trailing silence (at most 200 ms), at
        the caller's rate. A pause and the utterance that confirms it (no speech in between) give the same audio."""
        tail_keep = min(self._silence, max(1, 200 // self.frame_ms))
        frames = self._frames[:len(self._frames) - self._silence + tail_keep]
        audio = b"".join(frames)
        if self.in_rate != self.vad_rate:  # report the audio at the rate the caller streams
            audio = float_to_pcm16(resample(pcm16_to_float(audio), self.vad_rate, self.in_rate))
        return audio

    def _finish(self) -> VadEvent:
        cfg = self.cfg
        speech_ms = self._voiced * self.frame_ms
        kind = "utterance" if speech_ms >= cfg.min_speech_ms else "noise"
        audio = self._audio_so_far() if kind == "utterance" else b""
        at = self.pos_ms
        self._clear_utterance()
        self._last_speech = False
        return VadEvent(kind, audio=audio, speech_ms=speech_ms, at_ms=at)
