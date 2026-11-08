"""Where the hosted language model and the embeddings are reached from: one place for the base URLs, the model ids and the
keys, so llm.py and retrieval.py agree. Nothing here makes a request, and no endpoint or model id is written in code: they
are set in backend/.env and a missing one is reported by name.

Language model (an OpenAI-compatible Chat Completions endpoint, used by services/llm.py):
  LLM_BASE_URL   the endpoint's base URL (required)
  LLM_MODEL      the model id (required)
  LLM_API_KEY    the key (required, secret; one key, never rotated)
  (the older names LLM_PRIMARY_BASE_URL, LLM_PRIMARY_MODEL and LLM_PRIMARY_KEY are still read when the new ones are unset)

Embeddings (the batch-embed REST shape: POST {base}/models/{model}:batchEmbedContents, body {"requests": [{"model":
"models/{model}", "content": {"parts": [{"text": ...}]}, "outputDimensionality": N}, ...]}, the key in the x-goog-api-key
header; services/retrieval.py builds it):
  EMBEDDINGS_BASE_URL   the endpoint's base URL (required)
  EMBEDDINGS_MODEL      the model id (required)
  EMBEDDINGS_DIM        the vector size, 128..3072, default 768
  EMBEDDINGS_API_KEY / EMBEDDINGS_API_KEYS   one key, or several separated by commas, spaces or newlines

The embedding keys form one pool. The pool sticks to its current key until that key fails (a quota, a rate limit or an invalid
key); the failed key then cools down for a while and the next one takes over at once, so a turn is not lost to one account's
quota. Keys are never logged in full (the last four characters only) or returned by status.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Dict, List, Optional

log = logging.getLogger("vakya.model_config")

DEFAULT_EMBED_DIM = 768


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _first(*names: str) -> str:
    for n in names:
        v = _env(n)
        if v:
            return v.strip("\"'")
    return ""


# ---- the language model ----

def base_url() -> str:
    """LLM_BASE_URL (empty when unset)."""
    return _first("LLM_BASE_URL", "LLM_PRIMARY_BASE_URL")


def chat_model() -> str:
    """LLM_MODEL (empty when unset)."""
    return _first("LLM_MODEL", "LLM_PRIMARY_MODEL")


def api_key() -> str:
    """LLM_API_KEY (quotes stripped), empty when unset. One key, never rotated."""
    return _first("LLM_API_KEY", "LLM_PRIMARY_KEY")


def llm_missing() -> Optional[str]:
    """What is not set for the language model, as one plain sentence; None when it is configured."""
    missing = [n for n, v in (("LLM_API_KEY", api_key()), ("LLM_BASE_URL", base_url()), ("LLM_MODEL", chat_model())) if not v]
    return None if not missing else "Needs a language model: set " + ", ".join(missing) + " in backend/.env"


def llm_configured() -> bool:
    return llm_missing() is None


# ---- the key pool ----
# Cool-down lengths, in seconds. A rate limit (429) waits the retryDelay the server names, else 60 s; a per-day quota is not back
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
    """The embeddings key pool: EMBEDDINGS_API_KEY (if set) followed by EMBEDDINGS_API_KEYS (separated by commas, spaces or
    newlines), without duplicates, in that order."""
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


def embed_key() -> str:
    """The embeddings key: the pool's current key. Empty when none is set."""
    return pick_key()


def embed_base_url() -> str:
    """EMBEDDINGS_BASE_URL, without a trailing slash (empty when unset)."""
    return _env("EMBEDDINGS_BASE_URL").rstrip("/")


def embed_model() -> str:
    """EMBEDDINGS_MODEL (empty when unset)."""
    return _env("EMBEDDINGS_MODEL")


def embed_dim() -> int:
    """EMBEDDINGS_DIM (128..3072), else 768."""
    try:
        n = int(_env("EMBEDDINGS_DIM", str(DEFAULT_EMBED_DIM)))
    except ValueError:
        n = DEFAULT_EMBED_DIM
    return max(128, min(3072, n))


def embeddings_missing() -> Optional[str]:
    """What is not set for the embeddings, as one plain sentence; None when they are configured."""
    missing = [n for n, v in (("EMBEDDINGS_API_KEY", embed_key()), ("EMBEDDINGS_BASE_URL", embed_base_url()),
                              ("EMBEDDINGS_MODEL", embed_model())) if not v]
    return None if not missing else "Needs embeddings: set " + ", ".join(missing) + " in backend/.env"


def embeddings_configured() -> bool:
    return embeddings_missing() is None
