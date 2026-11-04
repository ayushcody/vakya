"""Refreshing a sign-in token through the hosted auth provider."""


from fastapi import APIRouter, Request
from pydantic import BaseModel

try:
    import httpx
except Exception:  # noqa: BLE001
    httpx = None

try:
    from core import state
    from core.errors import AppError
    from core.ratelimit import limiter
    from core.validation import sanitize_text
except ImportError:  # pragma: no cover
    from backend.core import state
    from backend.core.errors import AppError
    from backend.core.ratelimit import limiter
    from backend.core.validation import sanitize_text


router = APIRouter()


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/api/auth/refresh")
@limiter.limit("30/minute")
async def auth_refresh(request: Request, body: RefreshRequest):
    if not state.SUPABASE_URL or httpx is None:
        raise AppError("AUTH_UNAVAILABLE", "Authentication provider is not configured", 503)
    token = sanitize_text(body.refresh_token, "refresh_token", 4096)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{state.SUPABASE_URL.rstrip('/')}/auth/v1/token",
                params={"grant_type": "refresh_token"},
                headers={"apikey": state.SUPABASE_ANON_KEY or "", "Content-Type": "application/json"},
                json={"refresh_token": token},
            )
    except httpx.HTTPError as e:
        raise AppError("AUTH_UNAVAILABLE", "Authentication provider is unreachable", 503) from e
    if r.status_code >= 500:
        raise AppError("AUTH_UNAVAILABLE", "Authentication provider error", 503)
    if r.status_code != 200:
        raise AppError("INVALID_REFRESH_TOKEN", "Refresh token is invalid or expired", 401)
    data = r.json()
    return {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expires_in": data.get("expires_in"),
        "expires_at": data.get("expires_at"),
        "token_type": data.get("token_type", "bearer"),
        "user_id": (data.get("user") or {}).get("id"),
    }
