"""Supabase storage and the small database helpers every router shares: the audio bucket, signed links, the project row and
a cloud call that turns any failure into a 500 DB_ERROR."""

import asyncio
import logging
from typing import Any, Optional

try:
    from core import state
    from core.errors import AppError
    from core.retry import retry_async
except ImportError:  # pragma: no cover
    from backend.core import state
    from backend.core.errors import AppError
    from backend.core.retry import retry_async

log = logging.getLogger("vakya")


def require_supabase():
    if state.supabase is None:
        raise AppError("STORAGE_UNAVAILABLE", "Supabase is not configured", 503)
    return state.supabase


async def sb_upload(path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    """Upload to Supabase Storage with exponential-backoff retry (upserts)."""
    sb = require_supabase()
    await retry_async(
        lambda: sb.storage.from_(state.AUDIO_BUCKET).upload(path, data, {"content-type": content_type, "upsert": "true"}),
        label=f"storage.upload:{path}",
    )


async def sb_download(path: str) -> bytes:
    """Download from Supabase Storage with retry. Missing objects raise immediately."""
    sb = require_supabase()
    return await retry_async(lambda: sb.storage.from_(state.AUDIO_BUCKET).download(path), label=f"storage.download:{path}")


def signed_audio_url(sb: Any, path: str) -> Optional[str]:
    """A signed link to a private-bucket object that works for SIGNED_URL_EXPIRES_S seconds (None when signing fails).
    Blocking: call it with asyncio.to_thread from async code."""
    try:
        res = sb.storage.from_(state.AUDIO_BUCKET).create_signed_url(path, state.SIGNED_URL_EXPIRES_S)
    except Exception as e:  # noqa: BLE001
        log.warning("could not sign audio URL for %s: %s", path, e)
        return None
    url = res.get("signedURL") or res.get("signedUrl") if isinstance(res, dict) else None
    if url and url.startswith("/"):  # older clients return a path relative to the storage API
        url = f"{state.SUPABASE_URL.rstrip('/')}/storage/v1{url}"
    return url


async def cloud_run(fn, what: str):
    """Run a blocking Supabase call off the event loop; any failure becomes a 500 DB_ERROR."""
    try:
        return await asyncio.to_thread(fn)
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("%s failed: %s", what, e)
        raise AppError("DB_ERROR", f"Database error: {what}", 500, {"reason": str(e)[:200]}) from e


async def get_or_create_project(user_id: str) -> str:
    sb = require_supabase()
    proj_res = await retry_async(
        lambda: sb.table("projects").select("id").eq("owner_user_id", user_id).limit(1).execute(),
        label="projects.select")
    if proj_res.data:
        return proj_res.data[0]["id"]
    new_proj = await retry_async(
        lambda: sb.table("projects").insert({"owner_user_id": user_id, "name": "Default Project"}).execute(),
        label="projects.insert")
    return new_proj.data[0]["id"]
