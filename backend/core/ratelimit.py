"""Rate limiting (slowapi): one limiter shared by every router, keyed by the signed-in user, else the client address. Without
slowapi installed `limiter.limit(...)` does nothing and the app still runs."""

import logging

import jwt
from fastapi import Request

try:
    from core import state
except ImportError:  # pragma: no cover
    from backend.core import state

log = logging.getLogger("vakya")


def rate_limit_key(request: Request) -> str:
    """Rate-limit bucket: authenticated user id when resolvable, else client IP."""
    ip = request.client.host if request.client else "unknown"
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and request.headers.get("storage-mode") != "local":
        token = auth.split(" ", 1)[1].strip()
        if state.SUPABASE_JWT_SECRET and token not in ("demo-token", "test"):
            try:
                sub = jwt.decode(token, state.SUPABASE_JWT_SECRET, algorithms=["HS256"], audience="authenticated").get("sub")
                if sub:
                    return f"user:{sub}"
            except Exception:  # noqa: BLE001
                pass
    return f"ip:{ip}"


try:
    from slowapi import Limiter
    limiter = Limiter(key_func=rate_limit_key)
    SLOWAPI = True
except ImportError:  # slowapi not installed: rate limiting disabled, app still runs
    log.warning("slowapi not installed - rate limiting disabled")
    SLOWAPI = False

    class _NoopLimiter:
        def limit(self, *_a, **_k):
            return lambda fn: fn

    limiter = _NoopLimiter()
