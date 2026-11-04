"""What the route handlers share: the signed-in user, the storage mode of a request and the checks on ids and language codes."""

import re

from fastapi import Header, Request

try:
    from core import state
    from core.errors import AppError
    from core.validation import sanitize_text
    from services.auth import get_current_user_id
except ImportError:  # pragma: no cover
    from backend.core import state
    from backend.core.errors import AppError
    from backend.core.validation import sanitize_text
    from backend.services.auth import get_current_user_id


async def get_current_user(request: Request, authorization: str = Header(None)):
    # Local mode / demo tokens / JWT verification live in services.auth (gated by env there).
    uid = await get_current_user_id(request)  # shared auth service: JWTs and the local/demo shortcuts
    request.state.user_id = uid
    return uid


ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def check_id(value: str, field: str = "id") -> str:
    """Reject anything that could escape the data directory when used in a file name."""
    if not value or not ID_RE.match(value):
        raise AppError("INVALID_ID", f"Invalid {field}", 422, {"field": field})
    return value


def storage_mode_of(request: Request) -> str:
    if request.headers.get("storage-mode", "cloud") == "local" or state.supabase is None:
        return "local"
    return "cloud"


LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


def check_language(language: str) -> str:
    language = sanitize_text(language, "language", 16)
    if not LANG_RE.match(language):
        raise AppError("INVALID_LANGUAGE", "Invalid language code", 422, {"field": "language"})
    return language.lower()
