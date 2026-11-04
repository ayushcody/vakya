"""Signed visitor sessions and the shared plumbing of the public call links (web call, phone call tokens).

Used by routers/web_call.py (the browser call page) and routers/phone.py (the signed stream tokens):
  * `sign_session` / `read_session`: HMAC-signed session tokens bound to a link, an origin and a random visitor id;
  * `SlidingLimiter`: in-memory sliding-window rate limits;
  * `normalize_origin`: a plain `scheme://host[:port]` or None;
  * `clean_event` / `visitor_events`: one server-sent event of the internal streaming turn, cut down for an anonymous
    visitor (no receipts, timings, models, retrieval or tool details);
  * `load_agent_row`: the agent row of a stored session, local store or Supabase.
The signing secret is VAKYA_SESSION_SECRET, else a random one created on first use in data/session_secret (mode 600).
"""
import asyncio
import base64
import collections
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

try:
    from core.errors import AppError
except ImportError:  # pragma: no cover - imported as backend.services.web_session
    from backend.core.errors import AppError

log = logging.getLogger("vakya.web_session")

SESSION_TTL_S = 600
RENEW_GRACE_S = 24 * 3600          # an expired token still proves who the visitor was for this long
MAX_UPLOAD_BYTES = 6 * 1024 * 1024  # a 30 s recording is well under 1 MB


def _now() -> float:
    return time.time()


def _b():
    """The running backend (core/runtime.py): every name is read from the module that owns it, at call time."""
    try:
        from core.runtime import runtime  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.core.runtime import runtime  # noqa: PLC0415
    return runtime


# --------------------------------------------------------------------------------------------------- origins

_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_origin(value: Optional[str]) -> Optional[str]:
    """`scheme://host[:port]`, lower case, default port dropped; None when it is not a plain origin.
    Paths, queries, credentials and wildcards are never accepted."""
    if not value:
        return None
    text = value.strip()
    if not text or text.lower() == "null" or "*" in text or len(text) > 200 or "://" not in text:
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if scheme not in ("http", "https") or not host or parts.username or parts.password:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    if not re.fullmatch(r"[a-z0-9.\-]+|\[[0-9a-f:]+\]", host if ":" not in host else f"[{host}]"):
        return None
    shown = f"[{host}]" if ":" in host else host
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        shown += f":{port}"
    return f"{scheme}://{shown}"


# ----------------------------------------------------------------------------------------------- session tokens

_secret_lock = threading.Lock()
_secret_cache: Optional[bytes] = None


def _secret() -> bytes:
    """VAKYA_SESSION_SECRET, else a random secret created on first use and kept in data/session_secret (mode 600)."""
    global _secret_cache
    env = os.getenv("VAKYA_SESSION_SECRET", "").strip()
    if env:
        return env.encode("utf-8")
    with _secret_lock:
        if _secret_cache is not None:
            return _secret_cache
        data_dir = getattr(_b(), "LOCAL_DATA_DIR", None) or os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "data")
        path = os.path.join(data_dir, "session_secret")
        value = ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                value = f.read().strip()
        except OSError:
            pass
        if not value:
            value = secrets.token_hex(32)
            try:
                os.makedirs(data_dir, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(value)
            except FileExistsError:
                with open(path, "r", encoding="utf-8") as f:
                    value = f.read().strip()
            except OSError as e:
                log.warning("could not persist the session secret (%s); sessions will not survive a restart", e)
        _secret_cache = value.encode("utf-8")
        return _secret_cache


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(payload: str) -> str:
    return _b64(hmac.new(_secret(), b"vakya-session-v1." + payload.encode("ascii"), hashlib.sha256).digest())


def sign_session(claims: Dict[str, Any]) -> str:
    payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{payload}.{_mac(payload)}"


def read_session(token: Optional[str], *, allow_expired_for: float = 0.0) -> Dict[str, Any]:
    """The claims of a validly signed token. Raises AppError SESSION_INVALID (bad / forged) or SESSION_EXPIRED.
    `allow_expired_for` accepts tokens expired less than that many seconds ago."""
    try:
        payload, mac = (token or "").split(".")
        if not hmac.compare_digest(mac, _mac(payload)):
            raise ValueError("signature")
        claims = json.loads(_unb64(payload))
        if not isinstance(claims, dict) or not all(k in claims for k in ("k", "a", "m", "o", "v", "exp")):
            raise ValueError("claims")
    except Exception:  # noqa: BLE001 - anything malformed is just an invalid token
        raise AppError("SESSION_INVALID", "This session is not valid. Start again.", 401) from None
    if float(claims["exp"]) + allow_expired_for < _now():
        err = AppError("SESSION_EXPIRED", "This session has expired. Start again.", 401)
        err.claims = claims  # for the caller only: never part of the response
        raise err
    return claims


def bearer(request) -> Optional[str]:
    auth = request.headers.get("authorization", "")
    return auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else None


# ------------------------------------------------------------------------------------------------- rate limits

class SlidingLimiter:
    """Counts hits in a sliding window per bucket, in memory. `consume` checks several buckets at once and records
    a hit in all of them only when none is over its limit (so a refused turn is not charged)."""

    def __init__(self, max_buckets: int = 50000):
        self._hits: Dict[str, "collections.deque[float]"] = {}
        self._lock = threading.Lock()
        self._max = max_buckets

    def consume(self, rules: List[Tuple[str, int, int]]) -> int:
        """rules: (bucket, limit, window seconds). Returns 0 when allowed, else seconds until a slot frees up."""
        now = _now()
        with self._lock:
            wait = 0
            for bucket, limit, window in rules:
                q = self._hits.get(bucket)
                if q is None:
                    continue
                while q and q[0] <= now - window:
                    q.popleft()
                if len(q) >= limit:
                    wait = max(wait, int(q[0] + window - now) + 1)
            if wait:
                return wait
            if len(self._hits) > self._max:
                self._prune(now)
            for bucket, _limit, window in rules:
                self._hits.setdefault(bucket, collections.deque()).append(now)
            return 0

    def _prune(self, now: float) -> None:
        for bucket in [b for b, q in self._hits.items() if not q or q[-1] <= now - 86400]:
            del self._hits[bucket]
        while len(self._hits) > self._max:  # still too many: forget the oldest buckets
            del self._hits[next(iter(self._hits))]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


TURN_OWNERS: "collections.OrderedDict[str, str]" = collections.OrderedDict()  # turn id -> visitor id
VISITOR_CONV: "collections.OrderedDict[str, str]" = collections.OrderedDict()  # visitor id -> conversation id


def _remember(store: "collections.OrderedDict[str, str]", key: str, value: str, cap: int = 4000) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > cap:
        store.popitem(last=False)


# --------------------------------------------------------------------------------------------------- storage

async def load_agent_row(mode: str, agent_id: str) -> Optional[dict]:
    b = _b()
    if mode == "local":
        return await asyncio.to_thread(b.local_db.get, "agents", agent_id)
    if b.supabase is None:
        return None
    try:
        res = await asyncio.to_thread(
            lambda: b.supabase.table("agents").select("*").eq("id", agent_id).limit(1).execute())
        return res.data[0] if res.data else None
    except Exception as e:  # noqa: BLE001
        log.warning("agent lookup failed: %s", e)
        return None


# ------------------------------------------------------------------------------------------------ visitor events

SAFE_ERRORS = {
    "NO_SPEECH": "I did not catch that. Please try again.",
    "ASR_UNAVAILABLE": "The assistant cannot hear right now. Please try again later.",
    "TURN_FAILED": "Something went wrong. Please try again.",
    "AGENT_NOT_FOUND": "This assistant is not available.",
}
_CLIP_URL_RE = re.compile(r"^/api/conversations/turns/([0-9a-f-]{36})/sentences/(\d+)/audio$")
_DONE_FIELDS = ("turn_id", "user_text", "agent_text", "language", "fallback", "interrupted")


def _event_parts(chunk: bytes) -> Optional[Tuple[str, dict]]:
    try:
        head, _, rest = chunk.decode("utf-8").partition("\n")
        if not head.startswith("event: ") or not rest.startswith("data: "):
            return None
        return head[7:].strip(), json.loads(rest[6:].strip())
    except Exception:  # noqa: BLE001
        return None


def sse_event(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def clean_event(chunk: bytes, visitor: str, clip_base: str) -> bytes:
    """One server-sent event of the internal turn, cut down for a visitor. `clip_base` is where the visitor fetches a
    local clip from (the web call link has its own, link-scoped route)."""
    parts = _event_parts(chunk)
    if parts is None:
        return b""  # anything unexpected is not forwarded
    name, data = parts
    if name == "transcript":
        return sse_event(name, {"user_text": data.get("user_text", ""), "language": data.get("language")})
    if name == "sentence":
        url = str(data.get("audio_url") or "")
        match = _CLIP_URL_RE.match(url)
        if match:  # a local clip: served by the link's session-checked route
            _remember(TURN_OWNERS, match.group(1), visitor)
            url = f"{clip_base}/{match.group(1)}/{match.group(2)}"
        elif not url.startswith("data:audio/"):
            url = ""
        out = {"index": data.get("index"), "text": data.get("text", ""), "audio_url": url}
        if data.get("filler"):
            out["filler"] = True
        return sse_event(name, out)
    if name == "done":
        if data.get("conversation_id"):
            _remember(VISITOR_CONV, visitor, str(data["conversation_id"]))
        return sse_event(name, {k: data.get(k) for k in _DONE_FIELDS})
    if name == "error":
        code = str(data.get("code") or "TURN_FAILED")
        return sse_event(name, {"code": code, "message": SAFE_ERRORS.get(code, SAFE_ERRORS["TURN_FAILED"])})
    return b""


async def visitor_events(body_iterator, visitor: str, clip_base: str):
    try:
        async for chunk in body_iterator:
            piece = clean_event(chunk, visitor, clip_base)
            if piece:
                yield piece
    finally:
        close = getattr(body_iterator, "aclose", None)
        if close is not None:
            await close()
