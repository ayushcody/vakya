"""Gnani AI as the speech provider: Prisma hears, Timbre speaks and clones. The language and voice maps, the keyterms and the
glue that turns Timbre's audio into what the turn code expects live here; the HTTP lives in gnani_client.py and the rules for
using it (one request at a time, retries, the breaker, the cache, the ledger, the held lines) in gnani_gateway.py. With no
GNANI_API_KEY nothing here can reach Gnani: every call raises "Needs a Gnani key". See docs/GNANI.md."""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

try:
    from services import gnani_client as client, gnani_hold_lines  # noqa: F401 - importing it registers the hook
except ImportError:  # pragma: no cover - running from a different cwd
    from backend.services import gnani_client as client, gnani_hold_lines  # noqa: F401

log = logging.getLogger("vakya.gnani")

TIMBRE_ENGINE = "timbre"
TIMBRE_CLONE_ENGINE = "timbre_clone"
API_ENGINES = (TIMBRE_ENGINE, TIMBRE_CLONE_ENGINE)
LICENCE = "Gnani AI API terms"

# the app's two-letter language codes -> Gnani's BCP-47 codes (the ten languages Prisma and Timbre share)
LANGUAGE_CODES: Dict[str, str] = {
    "hi": "hi-IN", "mr": "mr-IN", "bn": "bn-IN", "ta": "ta-IN", "te": "te-IN", "kn": "kn-IN", "ml": "ml-IN",
    "gu": "gu-IN", "pa": "pa-IN", "en": "en-IN",
}
TIMBRE_LANGUAGES = frozenset(LANGUAGE_CODES) | {"hi-en"}
# agent languages that mean "Hindi with English words": recognised as hi + en, spoken with the Hinglish voice
HINGLISH = ("hinglish", "hi-en", "hi_en")

DEFAULT_VOICES = "Nalini:hi,Kaveri:en,Poorvi:hi-en"
DEFAULT_STT_MODEL = "prisma-v3.0"
DEFAULT_STT_FORMAT = "codemix"
DEFAULT_TTS_MODEL = "timbre-v2.5"
PHONE_RATE = 8000
PREVIEW_RATE = 24000


# ------------------------------------------------------------------------------------------------------ settings

def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def configured() -> bool:
    return client.configured()


NEEDS_KEY_MESSAGE = "Needs a Gnani key"


def engine_unavailable_reason() -> Optional[str]:
    return None if configured() else "Needs a Gnani key: set GNANI_API_KEY to use Gnani AI's Prisma and Timbre (see docs/GNANI.md)."


def require_key() -> None:
    """Raise the clear "Needs a Gnani key" error (503, code PROVIDER_NOT_CONFIGURED) when no key is set."""
    if not configured():
        client.missing_key()


# --------------------------------------------------------------------------------------------- recognition inputs

def stt_model() -> str:
    return _env("GNANI_STT_MODEL", DEFAULT_STT_MODEL)


def stt_format() -> str:
    return _env("GNANI_STT_FORMAT", DEFAULT_STT_FORMAT)


def stt_language_code(languages: Optional[Iterable[str]], model: Optional[str] = None) -> str:
    """`language_code` for the agent's languages: "hi-IN,en-IN" style (at most four, prisma-v3.0 only), a single code
    when the agent has one language, "indic-auto" when none is known or the model takes only one code. "hinglish"
    means hi + en."""
    model = model or stt_model()
    codes: List[str] = []
    for raw in languages or []:
        low = str(raw or "").strip().lower()
        expanded = ["hi", "en"] if low in HINGLISH else [low.replace("_", "-").split("-")[0]]
        for lang in expanded:
            code = LANGUAGE_CODES.get(lang)
            if code and code not in codes:
                codes.append(code)
    codes = codes[:client.MAX_LANGUAGE_CODES]
    if not codes:
        return "indic-auto"
    if len(codes) > 1 and model != "prisma-v3.0":
        return "indic-auto"      # only prisma-v3.0 takes a list of codes
    return ",".join(codes)


def two_letter(code: Optional[str]) -> Optional[str]:
    """Gnani's "hi-IN" back to the app's "hi" (None for an empty or unknown code)."""
    low = str(code or "").strip().lower().split("-")[0]
    return low if low in LANGUAGE_CODES else None


def env_keyterms() -> List[str]:
    return [t.strip() for t in _env("GNANI_KEYTERMS").replace("\n", ",").split(",") if t.strip()]


def merge_keyterms(*groups: Iterable[str]) -> List[str]:
    """Keyterms de-duplicated (case-insensitive), in order, at most 200."""
    seen, out = set(), []
    for group in groups:
        for term in group or []:
            t = str(term or "").strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
    return out[:client.MAX_KEYTERMS]


def keyterms_from_entities(entities: Iterable[Any]) -> List[str]:
    """The agent's names, places and terms (query_understanding.Entity objects, most frequent first)."""
    return [str(getattr(e, "text", e)) for e in entities or []]


_SPELLED = re.compile(r"(?<![\w.])(?:[A-Z][ .]){1,}[A-Z](?![\w])")


def collapse_spelled_acronyms(text: str) -> str:
    """Prisma can return an acronym letter by letter ("O P D", "U.P.I"). Join runs of single capitals into one word. Runs of
    only A and I ("I A", "A I") are left alone: they are as likely to be the pronoun and the article. Off with
    GNANI_FIX_ACRONYMS=off."""
    if not text or _env("GNANI_FIX_ACRONYMS", "on").lower() in ("off", "0", "false", "no"):
        return text

    def join(m: "re.Match") -> str:
        letters = re.sub(r"[ .]", "", m.group(0))
        return letters if (len(letters) >= 3 or set(letters) - {"A", "I"}) else m.group(0)

    return _SPELLED.sub(join, text)


def transcribe(audio: bytes, *, filename: str = "audio.wav", content_type: str = "audio/wav",
               languages: Optional[Iterable[str]] = None, keyterms: Optional[Iterable[str]] = None,
               previous_text: Optional[str] = None, topic: Optional[str] = None,
               speaker_names: Optional[Iterable[str]] = None, substitution_map: Optional[Iterable[Any]] = None,
               request_id: Optional[str] = None) -> dict:
    """One utterance through Prisma with the configured model and format. Blocking (run it in a thread). Returns the
    client's dict with `language` mapped to the app's two-letter code (None when Gnani did not say). `topic`,
    `speaker_names`, `substitution_map` (the desk's learnt corrections, services/hearing.py) and `request_id` are the
    context fields of the page https://docs.gnani.ai/api/STT/speech-to-text.md; see gnani_client._stt_form."""
    out = client.transcribe(audio, language=stt_language_code(languages), model=stt_model(), fmt=stt_format(),
                            keyterms=merge_keyterms(env_keyterms(), keyterms or []), previous_text=previous_text,
                            topic=topic, speaker_names=speaker_names, substitution_map=substitution_map,
                            request_id=request_id, filename=filename, content_type=content_type)
    out["language"] = two_letter(out.get("language"))
    fixed = collapse_spelled_acronyms(out.get("text") or "")
    if fixed != out.get("text"):
        out["text_raw"], out["text"] = out.get("text"), fixed
    return out


# --------------------------------------------------------------------------------------------------- synthesis

def tts_model() -> str:
    return _env("GNANI_TTS_MODEL", DEFAULT_TTS_MODEL)


_LANGUAGE_NAMES = {"marathi": "mr", "mar": "mr", "hindi": "hi", "hin": "hi", "english": "en", "eng": "en"}


def tts_language_code(language: str, text: Optional[str] = None) -> str:
    """Timbre's `language` for an app language code ("hi" -> "hi-IN", "mr" -> "mr-IN", "hinglish" -> "hi-en").

    Never "auto" for Hindi or Marathi. With `language: auto` Gnani resolves Devanagari text to Hindi
    (https://docs.gnani.ai/api/TTS/tts-input-formating.md), so a Marathi reply would have its numbers read the Hindi way;
    Marathi therefore always names "mr-IN" explicitly. An unknown code is "auto", except that when the text is Devanagari
    and carries Marathi function words (and more of them than Hindi ones) it is "mr-IN", and other Devanagari text is "hi-IN"."""
    low = str(language or "").strip().lower().replace("_", "-")
    if low in HINGLISH:
        return "hi-en"
    short = _LANGUAGE_NAMES.get(low, low.split("-")[0])
    if short in LANGUAGE_CODES:
        return LANGUAGE_CODES[short]
    if text and any("\u0900" <= ch <= "\u097f" for ch in text):
        try:
            from services import call_events   # noqa: PLC0415
        except ImportError:  # pragma: no cover
            from backend.services import call_events   # noqa: PLC0415
        lang, _sure = call_events.devanagari_language(text)
        return LANGUAGE_CODES[lang]
    return "auto"


def voices() -> List[Tuple[str, str]]:
    """GNANI_VOICES as [(voice name, language)], "Name:lang,Name:lang" (language hi, en, mr, ... or hi-en)."""
    out = []
    for item in _env("GNANI_VOICES", DEFAULT_VOICES).split(","):
        name, _, lang = item.strip().partition(":")
        if name.strip():
            out.append((name.strip(), (lang.strip().lower() or "hi")))
    return out


def voice_for(language: str) -> Optional[str]:
    """The voice that speaks `language`: the first GNANI_VOICES entry for it, else any (Timbre voices are
    one-language each on v2.5, so a mismatch sounds foreign but still works), else None."""
    low = str(language or "").strip().lower().replace("_", "-")
    target = "hi-en" if low in HINGLISH else low.split("-")[0]
    table = voices()
    for name, lang in table:
        if lang == target:
            return name
    return table[0][0] if table else None


def audio_to_float(data: bytes, fmt: str, sample_rate: int) -> Tuple[np.ndarray, int]:
    """Timbre's audio bytes as (mono float32, sample rate). `raw` is PCM16 at `sample_rate`; `wav` is parsed (PCM16 or
    float)."""
    if fmt == "raw":
        return np.frombuffer(data[:len(data) - (len(data) % 2)], dtype="<i2").astype(np.float32) / 32768.0, sample_rate
    import io
    import soundfile as sf  # noqa: PLC0415
    x, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=False)
    if getattr(x, "ndim", 1) > 1:
        x = x.mean(axis=1)
    return np.asarray(x, dtype=np.float32), int(sr)


def synth(text: str, language: str, *, phone: bool = False, speaker_embedding: Optional[List[float]] = None,
          speed: float = 1.0, voice: Optional[str] = None) -> Tuple[np.ndarray, int]:
    """Speak `text` with Timbre (or the cloned voice when an embedding is given). Blocking (run it in a thread).
    Preview and web audio: 24 kHz WAV. Phone audio: raw PCM16 at 8 kHz, the rate the phone line carries (the call
    path resamples to the call's rate; 8 kHz needs no change)."""
    rate, fmt = (PHONE_RATE, "raw") if phone else (PREVIEW_RATE, "wav")
    data = client.synthesize(text, voice=None if speaker_embedding else (voice or voice_for(language)),
                             language=tts_language_code(language, text), speed=speed, sample_rate=rate, fmt=fmt,
                             model=tts_model(), speaker_embedding=speaker_embedding)
    return audio_to_float(data, fmt, rate)
