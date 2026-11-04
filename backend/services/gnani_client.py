"""A small client for Gnani AI's Vachana speech API (host api.vachana.ai): Prisma speech recognition, Timbre speech
synthesis and voice-clone embeddings.

THIS MODULE HAS NEVER TALKED TO THE REAL API (no key was available when it was written). The request shapes follow Gnani's
public documentation as summarised in docs/GNANI_CHALLENGE_PLAN.md; every field name that was a best guess is isolated
in the `_stt_form`, `_tts_body`, `_embedding_form` and `_audio_from_response` helpers below, so the first live call
that fails on a field name is a one-line fix. Set GNANI_API_KEY to make it live; nothing here is imported on a hot path
without that key.

    transcribe(wav_bytes, language="hi-IN,en-IN", fmt="codemix", keyterms=[...]) -> {"text", "language", ...}
    synthesize("नमस्ते", voice="Nalini", language="hi-IN", sample_rate=24000, fmt="wav") -> audio bytes
    clone_embedding(wav_bytes) -> 768 floats
    StreamingRecognizer(...)  the realtime WebSocket (async), 1,024-byte PCM frames

Every request goes through services/gnani_gateway.py (one active request at a time, priority lanes, retries with
backoff, a circuit breaker, a response cache, the usage ledger); the three public functions below are thin doors onto
it and the `_..._raw` functions do the HTTP. A refusal by the gateway leaves as the same codes as a failure here.

Errors leave as `AppError` (core/errors.py): 402 -> PROVIDER_CREDITS (out of credits), 429 -> RATE_LIMITED, 401/403 ->
PROVIDER_AUTH, a timeout or an unreachable host -> PROVIDER_UNAVAILABLE, anything else 4xx/5xx -> PROVIDER_ERROR. The API
key travels only in the X-API-Key-ID header and is never logged or put in an error message."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import threading
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional

try:
    import httpx
except ImportError:  # pragma: no cover - httpx is in requirements.txt
    httpx = None

try:
    from core import offline
    from core.errors import AppError
    from services import gnani_gateway as gateway
except ImportError:  # pragma: no cover - running from a different cwd
    from backend.core import offline
    from backend.core.errors import AppError
    from backend.services import gnani_gateway as gateway

log = logging.getLogger("vakya.gnani")

DEFAULT_BASE_URL = "https://api.vachana.ai"
STT_PATH = "/stt/v3"
STT_STREAM_PATH = "/stt/v3/stream"
TTS_PATH = "/api/v1/tts/inference"
EMBEDDING_PATH = "/api/v1/tts/voice-clone/embeddings"

STT_MAX_SECONDS = 30            # the REST endpoint takes at most 30 s of audio
MAX_KEYTERMS = 200
MAX_LANGUAGE_CODES = 4          # prisma-v3.0 takes up to four codes as a hint
FRAME_BYTES = 1024              # the realtime socket wants exactly 1,024-byte frames of 16-bit mono PCM
STREAM_RATES = (8000, 16000, 44100, 48000)
TTS_RATES = (8000, 16000, 22050, 24000, 44100, 48000)
TTS_FORMATS = ("wav", "raw", "mp3", "ogg", "mulaw", "alaw")
SPEED_RANGE = (0.85, 1.15)
EMBEDDING_DIMS = 768


# ------------------------------------------------------------------------------------------------------ settings

def api_key() -> str:
    return (os.getenv("GNANI_API_KEY") or "").strip()


def configured() -> bool:
    return bool(api_key())


def base_url() -> str:
    return (os.getenv("GNANI_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")


def timeout_s() -> float:
    try:
        return max(1.0, float(os.getenv("GNANI_TIMEOUT_S") or 20))
    except ValueError:
        return 20.0


def missing_key() -> None:
    """Raise the "Needs a Gnani key" error every speaking and listening call gives when no key is set."""
    raise AppError("PROVIDER_NOT_CONFIGURED", "Needs a Gnani key: set GNANI_API_KEY in backend/.env (docs/GNANI.md).", 503,
                   {"provider": "gnani", "needs_key": True})


def _headers() -> Dict[str, str]:
    key = api_key()
    if not key:
        missing_key()
    return {"X-API-Key-ID": key}


# ------------------------------------------------------------------------------------------------- http plumbing

_pool: Optional["httpx.Client"] = None
_pool_lock = threading.Lock()


def _client() -> "httpx.Client":
    """The shared keep-alive client (tests replace this function with one over httpx.MockTransport)."""
    global _pool
    if httpx is None:  # pragma: no cover
        raise AppError("PROVIDER_UNAVAILABLE", "httpx is not installed.", 503, {"provider": "gnani"})
    with _pool_lock:
        if _pool is None:
            _pool = httpx.Client(limits=httpx.Limits(max_connections=16, max_keepalive_connections=4,
                                                     keepalive_expiry=30.0))
        return _pool


def _raise_for(status: int, body: Any, retry_after: Optional[float] = None) -> None:
    """Map an error status to the backend's AppError codes. The body is quoted (truncated) for the operator; it holds
    no secret (the key is only ever a request header)."""
    snippet = body if isinstance(body, str) else repr(body)
    snippet = snippet[:300]
    details = {"provider": "gnani", "status": status, "body": snippet}
    if retry_after:
        details["retry_after"] = retry_after
    if status == 402:
        raise AppError("PROVIDER_CREDITS", "Gnani AI reports the account is out of credits.", 402, details)
    if status == 429:
        raise AppError("RATE_LIMITED", "Gnani AI rate limit reached. Please slow down.", 429, details)
    if status in (401, 403):
        raise AppError("PROVIDER_AUTH", "Gnani AI rejected the API key.", 502, details)
    raise AppError("PROVIDER_ERROR", f"Gnani AI returned HTTP {status}.", 502, details)


def _retry_after(resp: "httpx.Response") -> Optional[float]:
    """Seconds from a numeric Retry-After header (None for a missing, dated or nonsense value)."""
    try:
        value = float(resp.headers.get("retry-after") or "")
    except (TypeError, ValueError):
        return None
    return value if 0 < value <= 60 else None


def _request(method: str, path: str, **kw) -> "httpx.Response":
    offline.block("Gnani AI speech")
    try:
        headers = {**kw.pop("headers", {}), **_headers()}
        resp = _client().request(method, base_url() + path, headers=headers,
                                 timeout=kw.pop("timeout", None) or timeout_s(), **kw)
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001 - httpx timeouts / connection errors
        name = type(e).__name__
        if httpx is not None and isinstance(e, httpx.TimeoutException):
            raise AppError("PROVIDER_UNAVAILABLE", "Gnani AI timed out.", 504,
                           {"provider": "gnani", "error": name}) from e
        raise AppError("PROVIDER_UNAVAILABLE", "Gnani AI could not be reached.", 503,
                       {"provider": "gnani", "error": name}) from e
    if resp.status_code >= 400:
        try:
            body = resp.text
        except Exception:  # noqa: BLE001
            body = ""
        _raise_for(resp.status_code, body, _retry_after(resp))
    return resp


# ------------------------------------------------------------------------------------------ speech recognition

MAX_SUBSTITUTION_RULES = 10     # https://docs.gnani.ai/api/STT/speech-to-text.md: substitution_map holds at most 10 rules
MAX_SPEAKER_NAMES = 4           # speaker_names takes 1 to 4 names
MAX_TOPIC_CHARS = 1600          # topic: non-empty, at most 1,600 characters
MAX_PREVIOUS_TEXT_CHARS = 600   # previous_text: the last 600 characters are kept
REQUEST_ID_HEADER = "X-API-Request-ID"


def _clean_list(values: Optional[Iterable[str]], limit: int) -> List[str]:
    out: List[str] = []
    for v in values or []:
        t = str(v or "").strip()
        if t and t not in out:
            out.append(t)
    return out[:limit]


def substitution_rules(rules: Optional[Iterable[Any]]) -> List[dict]:
    """The `substitution_map` rules Gnani wants, `[{"patterns": [...], "replacement": "..."}]`, from rules written either
    that way or as `{"heard": ..., "meant": ...}` (the desk's hearing store, services/hearing.py). Empty or malformed rules are
    dropped and at most 10 are kept."""
    out: List[dict] = []
    for r in rules or []:
        if not isinstance(r, dict):
            continue
        patterns = r.get("patterns") if isinstance(r.get("patterns"), list) else [r.get("heard")]
        replacement = r.get("replacement") if "replacement" in r else r.get("meant")
        pats = _clean_list(patterns, 20)
        rep = str(replacement or "").strip()
        if pats and rep:
            out.append({"patterns": pats, "replacement": rep})
    return out[:MAX_SUBSTITUTION_RULES]


def _stt_form(language: str, model: str, fmt: str, keyterms: Optional[Iterable[str]], topic: Optional[str],
              previous_text: Optional[str], speaker_names: Optional[Iterable[str]] = None,
              substitution_map: Optional[Iterable[Any]] = None) -> Dict[str, str]:
    """The multipart text fields of `POST /stt/v3` (https://docs.gnani.ai/api/STT/speech-to-text.md). Encodings as documented:
    `keyterms` and `speaker_names` are JSON arrays of strings (the page also accepts comma-separated; JSON keeps a phrase
    with a comma whole), at most 200 and 4; `topic` non-empty text of at most 1,600 characters; `previous_text` non-empty,
    Gnani keeps its last 600 characters; `enable_substitution` the string "true" and `substitution_map` a JSON list of
    `{"patterns": [..], "replacement": ".."}` (at most 10 rules), sent together and only when there is a rule. Empty values are
    left out (the page says a value outside its limits is a 400 INVALID_CONTEXT)."""
    form = {"language_code": language, "model": model, "format": fmt}
    terms = _clean_list(keyterms, MAX_KEYTERMS)
    if terms:
        form["keyterms"] = json.dumps(terms, ensure_ascii=False)
    topic = (topic or "").strip()
    if topic:
        form["topic"] = topic[:MAX_TOPIC_CHARS]
    prev = (previous_text or "").strip()
    if prev:
        form["previous_text"] = prev[-MAX_PREVIOUS_TEXT_CHARS:]
    names = _clean_list(speaker_names, MAX_SPEAKER_NAMES)
    if names:
        form["speaker_names"] = json.dumps(names, ensure_ascii=False)
    rules = substitution_rules(substitution_map)
    if rules:
        form["enable_substitution"] = "true"
        form["substitution_map"] = json.dumps(rules, ensure_ascii=False)
    return form


def transcribe(audio: bytes, *, language: str = "indic-auto", model: str = "prisma-v3.0", fmt: str = "codemix",
               keyterms: Optional[Iterable[str]] = None, topic: Optional[str] = None,
               previous_text: Optional[str] = None, filename: str = "audio.wav",
               content_type: str = "audio/wav", lane: Optional[str] = None,
               speaker_names: Optional[Iterable[str]] = None, substitution_map: Optional[Iterable[Any]] = None,
               request_id: Optional[str] = None) -> dict:
    """One utterance (wav/mp3/ogg/flac, at most 30 s) to text. Returns the API's JSON plus normalised keys: `text`
    (stripped, "" when none), `language` (detected_language_code, or None), `request_id` (what Gnani echoed from our
    `X-API-Request-ID` header, else the id it made), `processing_time` (seconds, or None) and `provider` ("gnani"). Blocking:
    call it with asyncio.to_thread from async code. Goes through the gateway (cache, queue, retries, breaker, ledger). The
    request id is a correlation label only: it is not part of the cache key."""
    terms = _clean_list(keyterms, MAX_KEYTERMS)
    names = _clean_list(speaker_names, MAX_SPEAKER_NAMES)
    rules = substitution_rules(substitution_map)
    key = gateway.cache_key("stt", language, model, fmt, terms, topic, previous_text, names, rules, blobs=(audio,))
    return gateway.execute(
        "stt", lambda: _transcribe_raw(audio, language=language, model=model, fmt=fmt, keyterms=terms, topic=topic,
                                       previous_text=previous_text, filename=filename, content_type=content_type,
                                       speaker_names=names, substitution_map=rules, request_id=request_id),
        units={"seconds": gateway.audio_seconds(audio)}, lane=lane, key=key,
        size=lambda r: len(json.dumps(r, default=str)), configured=configured())


def request_id_for(conversation_id: Optional[str], turn: Optional[Any] = None) -> Optional[str]:
    """The correlation id Vakya sends as `X-API-Request-ID` and Gnani echoes as `request_id`: "<conversation id>:<turn>"
    (printable ASCII only, at most 100 characters), or None when neither part is known."""
    def part(value: Any, limit: int) -> str:
        return re.sub(r"[^A-Za-z0-9_.\-]", "", str(value if value is not None else ""))[:limit]
    parts = [x for x in (part(conversation_id, 70), part(turn, 20)) if x]
    return ":".join(parts) or None


def _transcribe_raw(audio: bytes, *, language: str, model: str, fmt: str, keyterms: Optional[Iterable[str]],
                    topic: Optional[str], previous_text: Optional[str], filename: str, content_type: str,
                    speaker_names: Optional[Iterable[str]] = None, substitution_map: Optional[Iterable[Any]] = None,
                    request_id: Optional[str] = None) -> dict:
    extra = {}
    rid = (request_id or "").strip()
    if rid:
        extra["headers"] = {REQUEST_ID_HEADER: rid}
    resp = _request("POST", STT_PATH, files={"audio_file": (filename, audio, content_type)},
                    data=_stt_form(language, model, fmt, keyterms, topic, previous_text, speaker_names, substitution_map),
                    **extra)
    try:
        data = resp.json()
    except ValueError as e:
        raise AppError("PROVIDER_ERROR", "Gnani AI returned a speech-recognition reply that is not JSON.", 502,
                       {"provider": "gnani"}) from e
    if not isinstance(data, dict):
        raise AppError("PROVIDER_ERROR", "Gnani AI returned an unexpected speech-recognition reply.", 502,
                       {"provider": "gnani"})
    proc = data.get("processing_time")
    return {**data, "text": str(data.get("text") or "").strip(),
            "language": data.get("detected_language_code") or None,
            "request_id": data.get("request_id") or rid or None,
            "processing_time": proc if isinstance(proc, (int, float)) and not isinstance(proc, bool) else None,
            "provider": "gnani"}


# --------------------------------------------------------------------------------------------- speech synthesis

def _tts_body(text: str, voice: Optional[str], language: str, speed: float, sample_rate: int, fmt: str,
              model: str, speaker_embedding: Optional[List[float]]) -> dict:
    """GUESS: `voice` is omitted for a cloned voice (the embedding names it), `speed` is clamped to 0.85..1.15."""
    body: Dict[str, Any] = {"text": text, "model": model, "language": language,
                            "speed": min(max(float(speed), SPEED_RANGE[0]), SPEED_RANGE[1]),
                            "audio_config": {"sample_rate": sample_rate, "format": fmt}}
    if speaker_embedding:
        body["speaker_embedding"] = list(speaker_embedding)
    elif voice:
        body["voice"] = voice
    return body


def _audio_from_response(resp: "httpx.Response") -> bytes:
    """The audio bytes of a TTS reply: the body itself, or (GUESS) a JSON body with base64 audio."""
    ctype = (resp.headers.get("content-type") or "").lower()
    if "json" in ctype:
        try:
            data = resp.json()
        except ValueError as e:
            raise AppError("PROVIDER_ERROR", "Gnani AI returned a speech reply that is neither audio nor JSON.", 502,
                           {"provider": "gnani"}) from e
        encoded = ""
        if isinstance(data, dict):
            encoded = next((str(data[k]) for k in ("audio", "audio_content", "audio_base64", "data")
                            if isinstance(data.get(k), str) and data[k]), "")
        if not encoded:
            raise AppError("PROVIDER_ERROR", "Gnani AI returned no audio.", 502, {"provider": "gnani"})
        return base64.b64decode(encoded)
    return resp.content


def synthesize(text: str, *, voice: Optional[str] = None, language: str = "hi-IN", speed: float = 1.0,
               sample_rate: int = 24000, fmt: str = "wav", model: str = "timbre-v2.5",
               speaker_embedding: Optional[List[float]] = None, lane: Optional[str] = None) -> bytes:
    """Speak `text`; returns the audio bytes in `fmt` (wav, raw PCM16, mp3, ogg, mulaw or alaw). With a
    `speaker_embedding` (see clone_embedding) the cloned voice speaks, with model "vachana-vc-v1" unless one is given.
    WAV replies get their placeholder header sizes rewritten (gateway.fix_wav_header). Goes through the gateway: the same
    request twice is answered from the cache."""
    if sample_rate not in TTS_RATES:
        raise AppError("VALIDATION_ERROR", f"Timbre sample rate must be one of {TTS_RATES}.", 422)
    if fmt not in TTS_FORMATS:
        raise AppError("VALIDATION_ERROR", f"Timbre output format must be one of {TTS_FORMATS}.", 422)
    if speaker_embedding and model == "timbre-v2.5":
        model = "vachana-vc-v1"
    emb = json.dumps(list(speaker_embedding)).encode() if speaker_embedding else b""
    key = gateway.cache_key("tts", text, voice if not speaker_embedding else None, language, round(float(speed), 3),
                            sample_rate, fmt, model, blobs=(emb,))
    return gateway.execute(
        "tts", lambda: _synthesize_raw(text, voice, language, speed, sample_rate, fmt, model, speaker_embedding),
        units={"chars": len(text)}, lane=lane, key=key, size=len, configured=configured())


def _synthesize_raw(text: str, voice: Optional[str], language: str, speed: float, sample_rate: int, fmt: str,
                    model: str, speaker_embedding: Optional[List[float]]) -> bytes:
    resp = _request("POST", TTS_PATH, json=_tts_body(text, voice, language, speed, sample_rate, fmt, model,
                                                     speaker_embedding))
    audio = _audio_from_response(resp)
    if not audio:
        raise AppError("PROVIDER_ERROR", "Gnani AI returned empty audio.", 502, {"provider": "gnani"})
    if fmt == "wav":
        audio, fixed = gateway.fix_wav_header(audio)
        if fixed:
            log.info("rewrote the placeholder sizes in a Timbre WAV header (%d bytes)", len(audio))
    return audio


# ---------------------------------------------------------------------------------------------- voice cloning

def _embedding_form() -> dict:
    """GUESS: the clip goes in a multipart field named `audio_file` (the same name the STT endpoint uses)."""
    return {}


def clone_embedding(audio: bytes, *, filename: str = "reference.wav", content_type: str = "audio/wav",
                    lane: Optional[str] = None) -> List[float]:
    """The 768-dimensional speaker embedding of a 5 to 30 s clip (wav or mp3). Goes through the gateway."""
    return gateway.execute("clone", lambda: _clone_embedding_raw(audio, filename, content_type),
                           units={"seconds": gateway.audio_seconds(audio)}, lane=lane,
                           key=gateway.cache_key("clone", blobs=(audio,)), size=lambda v: 8 * len(v),
                           configured=configured())


def _clone_embedding_raw(audio: bytes, filename: str, content_type: str) -> List[float]:
    resp = _request("POST", EMBEDDING_PATH, files={"audio_file": (filename, audio, content_type)},
                    data=_embedding_form())
    try:
        data = resp.json()
    except ValueError as e:
        raise AppError("PROVIDER_ERROR", "Gnani AI returned an embedding reply that is not JSON.", 502,
                       {"provider": "gnani"}) from e
    emb = data.get("speaker_embedding") if isinstance(data, dict) else None
    if not isinstance(emb, list) or not emb:
        raise AppError("PROVIDER_ERROR", "Gnani AI returned no speaker embedding.", 502, {"provider": "gnani"})
    try:
        out = [float(x) for x in emb]
    except (TypeError, ValueError) as e:
        raise AppError("PROVIDER_ERROR", "Gnani AI returned a malformed speaker embedding.", 502,
                       {"provider": "gnani"}) from e
    if len(out) != EMBEDDING_DIMS:
        log.warning("Gnani speaker embedding has %d values (expected %d)", len(out), EMBEDDING_DIMS)
    return out


# ----------------------------------------------------------------------------------------- realtime recognition

def frame_pcm(pcm: bytes, frame_bytes: int = FRAME_BYTES) -> List[bytes]:
    """Raw PCM16 as exactly-1,024-byte frames; a short tail is padded with silence (the server rejects other sizes)."""
    frames = []
    for i in range(0, len(pcm), frame_bytes):
        chunk = pcm[i:i + frame_bytes]
        if len(chunk) < frame_bytes:
            chunk = chunk + b"\x00" * (frame_bytes - len(chunk))
        frames.append(chunk)
    return frames


def stream_headers(*, language: str = "indic-auto", model: str = "prisma-v3.0", sample_rate: int = 8000,
                   fmt: str = "codemix", vad_threshold: Optional[float] = None, min_silence_ms: Optional[int] = None,
                   min_speech_ms: Optional[int] = None) -> Dict[str, str]:
    """The WebSocket's configuration travels in headers (GUESS: names as in the plan, key header lower-cased)."""
    if sample_rate not in STREAM_RATES:
        raise AppError("VALIDATION_ERROR", f"The realtime sample rate must be one of {STREAM_RATES}.", 422)
    headers = {"x-api-key-id": api_key(), "language_code": language, "model": model,
               "x-sample-rate": str(sample_rate), "format": fmt}
    if vad_threshold is not None:
        headers["x-vad-threshold"] = str(vad_threshold)
    if min_silence_ms is not None:
        headers["x-min-silence-ms"] = str(int(min_silence_ms))
    if min_speech_ms is not None:
        headers["x-min-speech-ms"] = str(int(min_speech_ms))
    return headers


def stream_url() -> str:
    return base_url().replace("https://", "wss://", 1).replace("http://", "ws://", 1) + STT_STREAM_PATH


class StreamingRecognizer:
    """Realtime recognition over `wss://api.vachana.ai/stt/v3/stream` (needs the `websockets` package).

        async with StreamingRecognizer(sample_rate=8000, language="hi-IN") as rec:
            await rec.send_pcm(pcm)          # any length; sent as 1,024-byte frames at real-time pace
            await rec.commit()               # closes the utterance
            async for event in rec.events(): # connected, speech_start, processing, transcript {text, latency, ...}
                ...

    Sessions last at most 15 minutes: the caller reconnects. NOT YET RUN AGAINST THE REAL SERVER and not wired into the
    phone path (which transcribes each utterance as a WAV over REST)."""

    MAX_SESSION_S = 15 * 60

    def __init__(self, *, language: str = "indic-auto", model: str = "prisma-v3.0", sample_rate: int = 8000,
                 fmt: str = "codemix", vad_threshold: Optional[float] = None, min_silence_ms: Optional[int] = None,
                 min_speech_ms: Optional[int] = None, pace: bool = True, connect=None, lane: Optional[str] = None):
        self.sample_rate = sample_rate
        self.pace = pace
        self._headers = stream_headers(language=language, model=model, sample_rate=sample_rate, fmt=fmt,
                                       vad_threshold=vad_threshold, min_silence_ms=min_silence_ms,
                                       min_speech_ms=min_speech_ms)
        self._connect = connect   # tests pass a fake connector
        self._ws = None
        self._hold = None
        self._lane = lane

    async def __aenter__(self) -> "StreamingRecognizer":
        if configured():
            # the session holds the gateway's one slot until it closes (see services/gnani_gateway.py)
            self._hold = gateway.hold("stt_stream", self._lane)
            await self._hold.__aenter__()
        try:
            return await self._open()
        except BaseException as e:
            hold, self._hold = self._hold, None
            if hold is not None:
                await hold.__aexit__(type(e), e, None)
            raise

    async def _open(self) -> "StreamingRecognizer":
        connect = self._connect
        if connect is None:
            try:
                from websockets.asyncio.client import connect  # noqa: PLC0415 - websockets >= 13
            except ImportError as e:  # pragma: no cover
                raise AppError("PROVIDER_UNAVAILABLE", "The websockets package is not installed.", 503,
                               {"provider": "gnani"}) from e
        try:
            self._ws = await connect(stream_url(), additional_headers=self._headers)
        except Exception as e:  # noqa: BLE001
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status:
                _raise_for(int(status), "")
            raise AppError("PROVIDER_UNAVAILABLE", "Gnani AI's realtime recognition could not be reached.", 503,
                           {"provider": "gnani", "error": type(e).__name__}) from e
        return self

    async def __aexit__(self, *exc) -> bool:
        await self.close()
        return False

    async def send_pcm(self, pcm: bytes) -> None:
        """Send raw PCM16 mono as 1,024-byte frames, one per frame duration when `pace` (the server expects real time)."""
        frame_s = FRAME_BYTES / 2.0 / self.sample_rate
        for frame in frame_pcm(pcm):
            await self._ws.send(frame)
            if self._hold is not None:
                self._hold.add_seconds(frame_s)
            if self.pace:
                await asyncio.sleep(frame_s)

    async def commit(self) -> None:
        await self._ws.send(json.dumps({"type": "commit"}))

    async def events(self) -> AsyncIterator[dict]:
        """The server's JSON messages until the socket closes."""
        async for raw in self._ws:
            if isinstance(raw, (bytes, bytearray)):
                continue
            try:
                yield json.loads(raw)
            except ValueError:
                log.debug("ignoring a non-JSON realtime message")

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        hold, self._hold = self._hold, None
        if hold is not None:
            await hold.__aexit__(None, None, None)
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
