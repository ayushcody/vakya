"""Where Google's Embeddings is reached from: the language model (Embeddings Flash through the OpenAI-compatible endpoint) and the
embeddings that knowledge search uses. One place for the base URL, the model ids and the key, so llm.py and retrieval.py
agree. Nothing here makes a request.

Sources (read 4 Nov 2026):
  * OpenAI compatibility, base URL, tools and streaming: https://ai.google.dev/embeddings-api/docs/openai
      base URL      https://generativelanguage.googleapis.com/v1beta/openai/
      chat example  model "embeddings-3.8-flash", tools in the OpenAI format ({"type": "function", "function": {...}}),
                    tool_choice "auto", "role": "system" as the first message, stream=True (stream_options include_usage).
      The page documents no tool_choice other than "auto" and says nothing about parallel tool calls: send "auto", and
      handle one or several tool_calls in a reply (the calling code already loops over them).
  * Models: https://ai.google.dev/embeddings-api/docs/models  ("embeddings-3.6-flash" is the stable Flash, "embeddings-3.8-flash" is
      marked new, "embeddings-3-flash-preview" is a preview; "embeddings-embedding-2" and "embeddings-embedding-001" embed text).
  * Embeddings: https://ai.google.dev/embeddings-api/docs/embeddings
      REST  POST https://generativelanguage.googleapis.com/v1beta/models/embeddings-embedding-2:batchEmbedContents
            header x-goog-api-key; body {"requests": [{"model": "models/embeddings-embedding-2", "content": {"parts":
            [{"text": ...}]}, "outputDimensionality": N}, ...]}
      default 3072 dimensions, any 128..3072 (768, 1536, 3072 recommended); embeddings-embedding-2 normalises a truncated
      vector itself, embeddings-embedding-001 does not (so we always normalise); inputs over 8,192 tokens are truncated.
      embeddings-embedding-2 has no task_type: the text is prefixed instead ("task: search result | query: {text}" for a query,
      "title: {title} | text: {text}" for a passage).

Keys: EMBEDDINGS_API_KEY (one) and EMBEDDINGS_API_KEYS (several, separated by commas, spaces or newlines) form one pool. The pool
sticks to its current key until that key fails (a quota, a rate limit or an invalid key); the failed key then cools down for a
while and the next one takes over at once, so a turn is not lost to one account's quota. LLM_API_KEY, when set, is the language
model's only key and is never rotated. Keys are never logged in full (the last four characters only) or returned by status.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Dict, List, Optional

log = logging.getLogger("vakya.embeddings")

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_CHAT_MODEL = "embeddings-3.6-flash"
DEFAULT_EMBED_MODEL = "embeddings-embedding-2"
DEFAULT_EMBED_DIM = 768
EMBED_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"
GOOGLE_HOST = "generativelanguage.googleapis.com"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def base_url() -> str:
    """LLM_BASE_URL, else Embeddings's OpenAI-compatible endpoint."""
    return _env("LLM_BASE_URL", DEFAULT_BASE_URL)


def chat_model() -> str:
    """LLM_MODEL, else the stable Embeddings Flash."""
    return _env("LLM_MODEL", DEFAULT_CHAT_MODEL)


# ---- the key pool ----
# Cool-down lengths, in seconds. A rate limit (429) waits the retryDelay Google names, else 60 s; a per-day quota is not back
# before the next day, so it waits 6 h (the exact reset is midnight Pacific time; 6 h is the simple, safe approximation); a key
# that is invalid, expired or forbidden (401, 403, a 400 saying so) waits 24 h, which is the rest of the run.
COOLDOWN_RATE_LIMIT_S = 60.0
COOLDOWN_DAILY_QUOTA_S = 6 * 3600.0
COOLDOWN_BAD_KEY_S = 24 * 3600.0
_KEY_ERROR_RE = re.compile(r"API_KEY_INVALID|API key not valid|API key expired", re.IGNORECASE)
_DAILY_RE = re.compile(r"PerDay|per day|per_day", re.IGNORECASE)
_RETRY_DELAY_RE = re.compile(r'"retryDelay"\s*:\s*"([\d.]+)s"')

_pool_lock = threading.Lock()
_cooldown_until: Dict[str, float] = {}      # key -> time.monotonic() when it may be used again
_current: Optional[str] = None              # the key in use; it stays until it fails


def _clock() -> float:
    return time.monotonic()


def keys() -> List[str]:
    """The Embeddings key pool: EMBEDDINGS_API_KEY (if set) followed by EMBEDDINGS_API_KEYS (separated by commas, spaces or newlines),
    without duplicates, in that order."""
    out: List[str] = []
    for k in [_env("EMBEDDINGS_API_KEY"), *re.split(r"[,\s]+", os.getenv("EMBEDDINGS_API_KEYS") or "")]:
        k = k.strip().strip("\"'")
        if k and k not in out:
            out.append(k)
    return out


def _cooling(key: str) -> bool:
    return _cooldown_until.get(key, 0.0) > _clock()


def pick_key() -> str:
    """The pool key to use now: the current one while it is not cooling down, else the next one that is not. When every key is
    cooling down, the one whose cool-down ends soonest. Empty only when the pool is empty."""
    global _current
    pool = keys()
    if not pool:
        return ""
    with _pool_lock:
        start = pool.index(_current) if _current in pool else 0
        for i in range(len(pool)):
            k = pool[(start + i) % len(pool)]
            if not _cooling(k):
                _current = k
                return k
        _current = min(pool, key=lambda k: _cooldown_until.get(k, 0.0))
        return _current


def is_key_error(status: int, body: str = "") -> bool:
    """True when an HTTP answer means this key failed (not the request): 429, 401, 403, or a 400 that says the key is bad."""
    if status in (429, 401, 403):
        return True
    return status == 400 and bool(_KEY_ERROR_RE.search(body or ""))


def _cooldown_for(status: int, body: str) -> float:
    if status == 429:
        if _DAILY_RE.search(body or ""):
            return COOLDOWN_DAILY_QUOTA_S
        m = _RETRY_DELAY_RE.search(body or "")
        return float(m.group(1)) if m else COOLDOWN_RATE_LIMIT_S
    return COOLDOWN_BAD_KEY_S


def report_failure(key: str, status: int, body: str = "") -> None:
    """Put `key` on cool-down (see the lengths above) and move the pool on to its next key. A key outside the pool is ignored."""
    global _current
    pool = keys()
    if key not in pool:
        return
    wait = _cooldown_for(status, body)
    with _pool_lock:
        _cooldown_until[key] = _clock() + wait
        if len(pool) > 1 and _current == key:
            _current = pool[(pool.index(key) + 1) % len(pool)]
    log.warning("Embeddings key ending ...%s failed with HTTP %s; skipping it for %.0f s (%d of %d keys available)",
                key[-4:], status, wait, pool_status()["available"], len(pool))


def next_key_after_failure(key: str, status: int, body: str = "") -> Optional[str]:
    """For a caller whose request with `key` got `status`: report the failure when it is a key failure of a pool key, and
    return another pool key that is not cooling down to retry with at once, else None (the caller keeps its own retry rules)."""
    global _current
    if not key or not is_key_error(status, body) or key not in keys():
        return None
    report_failure(key, status, body)
    for k in keys():
        if k != key and not _cooling(k):
            with _pool_lock:
                _current = k
            return k
    return None


def pool_status() -> dict:
    """{"keys": how many, "available": how many are not cooling down}; never the keys themselves."""
    pool = keys()
    with _pool_lock:
        return {"keys": len(pool), "available": sum(1 for k in pool if not _cooling(k))}


def _reset_for_tests() -> None:
    """Forget every cool-down and the current key (the tests call this between cases)."""
    global _current
    with _pool_lock:
        _cooldown_until.clear()
        _current = None


def api_key() -> str:
    """The language-model key: LLM_API_KEY (one key, no rotation), else the pool's current key. Empty when neither is set."""
    return _env("LLM_API_KEY") or pick_key()


def uses_embeddings() -> bool:
    return GOOGLE_HOST in base_url()


def embed_key() -> str:
    """The embeddings key: the pool's current key, else LLM_API_KEY when the language model is Embeddings too. Empty when none."""
    return pick_key() or (_env("LLM_API_KEY") if uses_embeddings() else "")


def embed_model() -> str:
    """EMBEDDINGS_EMBED_MODEL, else embeddings-embedding-2."""
    return _env("EMBEDDINGS_EMBED_MODEL", DEFAULT_EMBED_MODEL)


def embed_dim() -> int:
    """EMBEDDINGS_EMBED_DIM (128..3072), else 768."""
    try:
        n = int(_env("EMBEDDINGS_EMBED_DIM", str(DEFAULT_EMBED_DIM)))
    except ValueError:
        n = DEFAULT_EMBED_DIM
    return max(128, min(3072, n))


def llm_configured() -> bool:
    return bool(api_key())


def embeddings_configured() -> bool:
    return bool(embed_key())


def missing_key_reason() -> Optional[str]:
    return None if llm_configured() else "Needs a Embeddings key (set EMBEDDINGS_API_KEY or EMBEDDINGS_API_KEYS)."
