"""Held lines: the two short sentences a live call can still say when Gnani cannot answer, rendered ahead of time by Timbre.

    "one_moment"   "One moment please."                                   a passing problem (busy, rate limit, a timeout)
    "callback"     "Sorry, the line is not clear. I will call you back."  Gnani is down or out of credits

There is no other voice to switch to, so when a live request is refused the turn plays one of these (the gateway names which in
the error: `details.hold_line`, `details.action`), offers the caller a callback or a person, and logs it. They are synthesised
by Timbre while it works (`prepare()`; it runs by itself in the background after the first served voice request unless
GNANI_PREPARE_HOLD_LINES=off) and kept on disk (VAKYA_HOLD_LINES_DIR, default backend/data/hold_lines), so they are there in
the middle of an outage and after a restart. Two formats per line: phone (raw 16-bit PCM, 8 kHz) and web (WAV, 24 kHz).

    audio = gnani_hold_lines.get("callback", "hi", phone=True)         # bytes or None when not prepared
    played = gnani_hold_lines.for_error(app_error, "hi", phone=True)    # {"line", "text", "audio", "action"} and a ledger line
"""
from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from core.errors import AppError
    from services import gnani_client as client, gnani_gateway as gateway, usage_ledger as ul
except ImportError:  # pragma: no cover - running from a different cwd
    from backend.core.errors import AppError
    from backend.services import gnani_client as client, gnani_gateway as gateway, usage_ledger as ul

log = logging.getLogger("vakya.gnani")

LINES: Dict[str, Dict[str, str]] = {
    "one_moment": {
        "en": "One moment please.",
        "hi": "कृपया एक क्षण रुकिए।",
        "mr": "कृपया एक क्षण थांबा.",
        "hi-en": "Ek second please.",
    },
    "callback": {
        "en": "Sorry, the line is not clear. I will call you back shortly.",
        "hi": "माफ़ कीजिए, लाइन साफ़ नहीं है। मैं आपको थोड़ी देर में वापस कॉल करती हूँ।",
        "mr": "माफ करा, लाइन स्पष्ट नाही. मी तुम्हाला थोड्या वेळाने परत फोन करते.",
        "hi-en": "Sorry, line clear nahi hai. Main aapko thodi der mein call back karti hoon.",
    },
}
CHANNELS = {"phone": ("raw", 8000), "web": ("wav", 24000)}
DEFAULT_LANGUAGES = "en,hi"

_mem: Dict[str, bytes] = {}
_lock = threading.Lock()
_auto_started = False


def directory() -> Path:
    env = (os.getenv("VAKYA_HOLD_LINES_DIR") or "").strip()
    return Path(env) if env else Path(__file__).resolve().parents[1] / "data" / "hold_lines"


def languages() -> List[str]:
    """The languages whose lines are prepared (HOLD_LINE_LANGUAGES, default en,hi; en, hi, mr and hi-en have text)."""
    raw = (os.getenv("GNANI_HOLD_LINE_LANGUAGES") or DEFAULT_LANGUAGES)
    out = [lang for lang in (normalise(x) for x in raw.split(",")) if lang]
    return list(dict.fromkeys(out)) or ["en"]


def normalise(language: Optional[str]) -> str:
    low = str(language or "").strip().lower().replace("_", "-")
    if low in ("hinglish", "hi-en"):
        return "hi-en"
    two = low.split("-")[0]
    return two if two in LINES["one_moment"] else "en"


def _safe(voice: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", voice or "default")[:40]


def _file(line: str, lang: str, channel: str, voice: str) -> Path:
    fmt, rate = CHANNELS[channel]
    return directory() / f"{line}.{lang}.{_safe(voice)}.{rate}.{fmt}"


def get(line: str, language: Optional[str], *, phone: bool = True, voice: str = "default") -> Optional[bytes]:
    """The prepared audio of a line, or None when it has not been prepared (the caller then speaks nothing and goes straight
    to offering the callback in text or a person)."""
    lang, channel = normalise(language), "phone" if phone else "web"
    path = _file(line, lang, channel, voice)
    key = str(path)
    with _lock:
        if key in _mem:
            return _mem[key]
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    with _lock:
        _mem[key] = data
    return data


def text_of(line: str, language: Optional[str]) -> str:
    return LINES[line][normalise(language)]


def status() -> Dict[str, Any]:
    """What is prepared, for GET /api/ready (reads the folder only)."""
    expected, present, missing = 0, 0, []
    for lang in languages():
        for line in LINES:
            for channel in CHANNELS:
                expected += 1
                if _file(line, lang, channel, "default").is_file():
                    present += 1
                else:
                    missing.append(f"{line}/{lang}/{channel}")
    return {"dir": str(directory()), "languages": languages(), "present": present, "expected": expected,
            "complete": present == expected, "missing_summary": ", ".join(missing[:6]) + (" ..." if len(missing) > 6 else "")}


def prepare(*, voice: Optional[str] = None, speaker_embedding: Optional[List[float]] = None, force: bool = False,
            lane: str = "desk") -> Dict[str, Any]:
    """Render every missing line with Timbre (about 8 short requests, a few credits) and keep them. `voice` is the voice
    name used in the file name; with a `speaker_embedding` the lines are in the cloned voice (pass a name that identifies it).
    Returns {"made": n, "failed": [..], **status()}. One failure does not stop the rest."""
    try:
        from services import gnani   # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.services import gnani   # noqa: PLC0415
    tag = voice or "default"
    made, failed = 0, []
    for lang in languages():
        for line, texts in LINES.items():
            for channel, (fmt, rate) in CHANNELS.items():
                path = _file(line, lang, channel, tag)
                if path.is_file() and not force:
                    continue
                try:
                    audio = client.synthesize(texts[lang], voice=None if speaker_embedding else gnani.voice_for(lang),
                                              language=gnani.tts_language_code(lang), sample_rate=rate, fmt=fmt,
                                              model=gnani.tts_model(), speaker_embedding=speaker_embedding, lane=lane)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_suffix(path.suffix + ".tmp")
                    tmp.write_bytes(audio)
                    tmp.replace(path)
                    with _lock:
                        _mem[str(path)] = audio
                    made += 1
                except Exception as e:  # noqa: BLE001 - report it, keep going
                    failed.append(f"{line}/{lang}/{channel}: {getattr(e, 'code', type(e).__name__)}")
    return {"made": made, "failed": failed, **status()}


def for_error(error: AppError, language: Optional[str], *, phone: bool = True, voice: str = "default") -> Optional[Dict[str, Any]]:
    """The line to play for a refused live request, and the ledger entry that says it was played. None when the error carries
    no plan (a request the caller got wrong, or not a Gnani error)."""
    details = error.details if isinstance(error.details, dict) else {}
    line = details.get("hold_line")
    if line not in LINES:
        return None
    scope = gateway.current_scope()
    ul.record_request(kind="hold_line", lane="live", credits=0.0, ok=True, cache_hit=True, degraded=details.get("degraded"),
                      action="hold_line_played" if line == "one_moment" else "callback_offered",
                      scope=scope.id if scope else None, conversation_id=scope.conversation_id if scope else None,
                      user=scope.user_id if scope else None)
    return {"line": line, "text": text_of(line, language), "audio": get(line, language, phone=phone, voice=voice),
            "action": details.get("action")}


def _auto(kind: str) -> None:
    """Gateway hook: the first time a voice request is served, prepare the lines in the background."""
    global _auto_started
    if kind != "tts" or _auto_started:
        return
    if (os.getenv("GNANI_PREPARE_HOLD_LINES") or "on").strip().lower() in ("off", "0", "false", "no"):
        return
    _auto_started = True
    threading.Thread(target=lambda: log.info("held lines prepared: %s", prepare()), name="gnani-hold-lines", daemon=True).start()


def reset() -> None:
    """Forget the in-memory copies and the auto-start flag (tests)."""
    global _auto_started
    with _lock:
        _mem.clear()
    _auto_started = False


gateway._after_success_hook = _auto
