"""Saved voices: the local voices file and Supabase rows, who owns a voice, the sealed voice files and embeddings on disk,
and deleting a voice with everything stored for it."""

import asyncio
import glob
import json
import logging
import os
import re
from typing import Any, List, Optional, Tuple

try:
    from core import deps, state
    from core.errors import AppError
    from core.retry import retry_async
    from services import storage, voice_vault
    from services.auth import auth_configured, LOCAL_USER
except ImportError:  # pragma: no cover
    from backend.core import deps, state
    from backend.core.errors import AppError
    from backend.core.retry import retry_async
    from backend.services import storage, voice_vault
    from backend.services.auth import auth_configured, LOCAL_USER

log = logging.getLogger("vakya")


def get_local_voices():
    if not os.path.exists(state.LOCAL_VOICES_FILE):
        return []
    with open(state.LOCAL_VOICES_FILE, "r") as f:
        return json.load(f)


def save_local_voice(voice_data):
    voices = get_local_voices()
    voices.append(voice_data)
    with open(state.LOCAL_VOICES_FILE, "w") as f:
        json.dump(voices, f, indent=4)


def update_local_voice(voice_id: str, **fields) -> None:
    voices = get_local_voices()
    for v in voices:
        if v.get("id") == voice_id:
            v.update(fields)
    with open(state.LOCAL_VOICES_FILE, "w") as f:
        json.dump(voices, f, indent=4)


VOICE_NOT_FOUND_MSG = "Voice embedding not found. Please re-clone this voice."


def owns_local(v: dict, user_id: Optional[str]) -> bool:
    # Legacy rows without user_id predate scoping: visible only in single-user local setups.
    if user_id is None:
        return True
    if not v.get("user_id"):
        return user_id == LOCAL_USER or not auth_configured()
    return v.get("user_id") == user_id


def _owns_cloud(row: dict, user_id: Optional[str]) -> bool:
    ref = row.get("reference_audio_path") or ""
    if user_id is None:
        return True
    if ref.startswith("local:"):
        return owns_local(row, user_id)
    return ref.split("/")[0] == user_id


async def lookup_voice(voice_id: str, storage_mode: str, user_id: Optional[str] = None) -> Optional[dict]:
    """Voice metadata row owned by user_id from local json / Supabase (None when unknown or foreign)."""
    local = next((v for v in get_local_voices() if v.get("id") == voice_id and owns_local(v, user_id)), None)
    if storage_mode == "local":
        return local
    if state.supabase is not None:
        try:
            res = await retry_async(
                lambda: state.supabase.table("voices").select("*").eq("id", voice_id).execute(), label="voices.select")
            if res.data:
                return res.data[0] if _owns_cloud(res.data[0], user_id) else None
        except Exception as e:  # noqa: BLE001
            log.warning("voice lookup failed: %s", e)
    return local


def voice_file_write(path: str, voice_id: str, label: str, data: bytes) -> None:
    """Write one of a voice's files sealed (services/voice_vault.py): the bytes on disk are ciphertext, atomically."""
    try:
        sealed = voice_vault.seal(voice_id, data, label)
    except voice_vault.VaultLocked as e:
        raise AppError("CONSENT_WITHDRAWN", "Consent for this voice was withdrawn.", 403, {"voice_id": voice_id}) from e
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as f:
        f.write(sealed)
    os.replace(tmp, path)


def voice_file_read(path: str, voice_id: str, label: str) -> bytes:
    """One of a voice's files, opened. A file written before the vault existed is plain: it is returned, and sealed in
    place so it is protected from now on. A voice whose consent was withdrawn (or whose key is gone) raises
    CONSENT_WITHDRAWN (403)."""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        data = voice_vault.unseal(voice_id, raw, label)
    except voice_vault.VaultLocked as e:
        raise AppError("CONSENT_WITHDRAWN", "Consent for this voice was withdrawn, so it can no longer be used.", 403,
                       {"voice_id": voice_id, "reason": e.reason}) from e
    if voice_vault.enabled() and not voice_vault.is_sealed(raw):
        try:
            voice_file_write(path, voice_id, label, data)
        except (OSError, AppError) as e:  # sealing in place is a bonus; the read already worked
            log.warning("could not seal %s: %s", os.path.basename(path), e)
    return data


UUID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")


def _remove_local_files(paths: List[str]) -> int:
    n = 0
    for p in paths:
        try:
            if os.path.isfile(p):
                os.remove(p)
                n += 1
        except OSError as e:
            log.warning("could not remove %s: %s", p, e)
    return n


def consent_record_path(user_id: str, consent_id: str) -> str:
    """Where a cloud voice's itemised consent record (JSON) is kept: next to the consent recording."""
    return f"{user_id}/consents/{consent_id}.json"


def _derived_voice_files(voice_id: str) -> List[str]:
    """Files built from a voice's recording (the Gnani speaker embedding). They hold the voice, so they go with it, in
    local and cloud mode alike."""
    return [os.path.join(state.LOCAL_AUDIO_DIR, f"{voice_id}{ext}") for ext in (".timbre.json",)]


async def delete_voice_core(voice_id: str, user_id: str, storage_mode: str) -> dict:
    """Remove a voice and everything stored for it (see delete_voice). 404 VOICE_NOT_FOUND when it is not the user's."""
    if storage_mode == "local":
        voices = get_local_voices()
        voice = next((v for v in voices if v.get("id") == voice_id and owns_local(v, user_id)), None)
        if voice is None:
            raise AppError("VOICE_NOT_FOUND", "Voice not found", 404)
        voice_vault.destroy(voice_id)   # crypto-shred first: even a copy of the files is unreadable from this moment
        paths = [os.path.join(state.LOCAL_AUDIO_DIR, f"{voice_id}{ext}") for ext in (".wav", ".pt")] + _derived_voice_files(voice_id)
        consent_id = str(voice.get("consent_id") or "")
        consents = 0
        if consent_id and UUID_RE.match(consent_id):
            consent_files = glob.glob(os.path.join(glob.escape(state.LOCAL_AUDIO_DIR), f"consent_{consent_id}.*"))
            paths += consent_files
            consents = 1
        remaining = [v for v in voices if v is not voice]
        with open(state.LOCAL_VOICES_FILE, "w") as f:
            json.dump(remaining, f, indent=4)
        files = await asyncio.to_thread(_remove_local_files, paths)

        def unlink_agents() -> int:
            n = 0
            for a in state.local_db.select("agents", lambda r: r.get("user_id") == user_id and r.get("voice_id") == voice_id):
                state.local_db.update("agents", a["id"], {"voice_id": None}, user_id)
                n += 1
            return n

        unlinked = await asyncio.to_thread(unlink_agents)
        return {"deleted": True, "message": "Voice deleted successfully",
                "removed": {"consents": consents, "files": files, "agents_unlinked": unlinked}}

    res = await retry_async(
        lambda: state.supabase.table("voices").select("*").eq("id", voice_id).execute(), label="voices.select")
    if not res.data or not _owns_cloud(res.data[0], user_id):
        raise AppError("VOICE_NOT_FOUND", "Voice not found", 404)
    voice = res.data[0]
    voice_vault.destroy(voice_id)

    storage_path = voice["reference_audio_path"]
    owner = storage_path.split("/")[0] if "/" in storage_path else user_id
    objects = [storage_path, f"{owner}/{voice_id}.pt", f"{owner}/{voice_id}.timbre.json"]
    consent_id = voice.get("consent_id")
    consent_paths: List[str] = []
    if consent_id and UUID_RE.match(str(consent_id)):
        consent_paths.append(consent_record_path(owner, str(consent_id)))
    if consent_id:
        try:
            cres = await retry_async(lambda: state.supabase.table("consents").select("audio_storage_path")
                                     .eq("id", consent_id).eq("user_id", user_id).execute(), label="consents.select")
            consent_paths += [c["audio_storage_path"] for c in (cres.data or []) if c.get("audio_storage_path")]
        except Exception as e:  # noqa: BLE001
            log.warning("could not look up consent record: %s", e)

    # Agents that spoke with this voice stop referencing it (the FK would null it, but do not rely on that).
    unlinked = 0
    try:
        ares = await retry_async(lambda: state.supabase.table("agents").select("id").eq("voice_id", voice_id)
                                 .eq("user_id", user_id).execute(), label="agents.select")
        unlinked = len(ares.data or [])
        if unlinked:
            await retry_async(lambda: state.supabase.table("agents").update({"voice_id": None})
                              .eq("voice_id", voice_id).eq("user_id", user_id).execute(), label="agents.unlink")
    except Exception as e:  # noqa: BLE001
        log.warning("could not unlink agents from voice %s: %s", voice_id, e)

    await retry_async(lambda: state.supabase.table("voices").delete().eq("id", voice_id).execute(), label="voices.delete")
    await asyncio.to_thread(_remove_local_files, _derived_voice_files(voice_id))
    consents = 0
    if consent_id:
        try:
            await retry_async(lambda: state.supabase.table("consents").delete().eq("id", consent_id)
                              .eq("user_id", user_id).execute(), label="consents.delete")
            consents = 1
        except Exception as e:  # noqa: BLE001
            log.warning("consent record not removed: %s", e)
    files = 0
    try:
        await retry_async(lambda: state.supabase.storage.from_(state.AUDIO_BUCKET).remove(objects + consent_paths),
                          label="storage.remove")
        files = len(objects) + len(consent_paths)
    except Exception as e:  # noqa: BLE001
        log.warning("voice files not removed from storage: %s", e)
    return {"deleted": True, "message": "Voice deleted successfully",
            "removed": {"consents": consents, "files": files, "agents_unlinked": unlinked}}


async def voice_consent(voice_id: Optional[str], storage_mode: str,
                         user_id: str) -> Tuple[Optional[str], Optional[bool], Optional[str]]:
    """(consent_id, consent_verified, voice display name) of a saved voice, for the receipt.

    Preset voices and unknown ids give (None, None, None): there is no consent record to point at."""
    if not voice_id:
        return None, None, None
    try:
        row = await lookup_voice(voice_id, storage_mode, user_id)
    except Exception as e:  # noqa: BLE001
        log.warning("consent lookup for the receipt failed: %s", e)
        return None, None, None
    if not row:
        return None, None, None
    consent_id = row.get("consent_id") or None
    name = row.get("display_name")
    if "consent_verified" in row:
        return consent_id, bool(row["consent_verified"]), name
    return consent_id, (True if consent_id else None), name  # cloud rows only carry a consent_id (set after the check)
