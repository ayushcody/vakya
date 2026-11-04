"""Agents and their documents as the routers see them: the JSON shape, ownership (404 for anyone else's), the tools setting and
the rows in Supabase."""

import asyncio
import logging
import os
from typing import List, Optional

from fastapi import Request

try:
    from core import deps, state
    from core.errors import AppError
    from services import storage, tools as tools_mod, voice_store
except ImportError:  # pragma: no cover
    from backend.core import deps, state
    from backend.core.errors import AppError
    from backend.services import storage, tools as tools_mod, voice_store

log = logging.getLogger("vakya")

LOCAL_PROJECT_ID = "local_project"


async def owned_agent(agent_id: str, user_id: str, columns: str = "*") -> dict:
    """Agent row owned by user_id, else 404 AGENT_NOT_FOUND."""
    sb = storage.require_supabase()
    try:
        res = await asyncio.to_thread(
            lambda: sb.table("agents").select(columns).eq("id", agent_id).eq("user_id", user_id).execute())
    except Exception as e:  # noqa: BLE001
        log.warning("agent lookup failed: %s", e)
        raise AppError("DB_ERROR", "Failed to look up agent", 500) from e
    if not res.data:
        raise AppError("AGENT_NOT_FOUND", "Agent not found", 404)
    return res.data[0]


def check_tools(value: Optional[List[str]]) -> List[str]:
    """An agent's `tools` setting: names of configured MCP tool servers (see services/tools.py)."""
    try:
        return tools_mod.clean_tool_names(value)
    except ValueError as e:
        raise AppError("INVALID_TOOLS", str(e), 422, {"field": "tools"}) from e


def agent_json(row: dict, document_count: int = 0) -> dict:
    langs = row.get("supported_languages") or ["en"]
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "greeting": row.get("greeting") or "",
        "system_instructions": row.get("system_instructions") or "",
        "supported_languages": [str(x) for x in langs],
        "voice_id": row.get("voice_id") or None,
        "disclose_ai": row.get("disclose_ai") is not False,  # agents saved before the setting existed disclose
        "tools": [str(x) for x in (row.get("tools") or [])],
        "template_id": row.get("template_id") or None,   # the front-desk type it was made from (null: from scratch)
        "created_at": row.get("created_at"),
        "document_count": int(document_count),
    }


def template_ids() -> List[str]:
    """The ids of the front-desk templates (the JSON files of backend/templates/agents)."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "agents")
    return sorted(f[:-5] for f in os.listdir(root) if f.endswith(".json"))


def check_template_id(value: Optional[str]) -> Optional[str]:
    """None or one of the template ids; anything else is a validation error."""
    if value in (None, ""):
        return None
    if str(value) not in template_ids():
        raise AppError("INVALID_TEMPLATE_ID", f"template_id must be one of {', '.join(template_ids())} or null.", 422,
                       {"field": "template_id"})
    return str(value)


def document_json(row: dict, chunk_count: int, question_count: Optional[int] = None) -> dict:
    """`question_count` is how many question-index rows the document has (services/question_index.py); `question_index` is
    the state of that job: off | skipped | pending | indexing | done | failed | none (an empty string for hosted documents)."""
    qc = question_count if question_count is not None else row.get("question_count")
    return {"id": row["id"], "filename": row.get("filename") or "", "chunk_count": int(chunk_count),
            "created_at": row.get("created_at"), "question_count": int(qc or 0),
            "question_index": row.get("question_index") or ""}


async def load_agent(request: Request, agent_id: str, user_id: str, columns: str = "*") -> dict:
    """Agent row owned by user_id in the request's storage mode, else 404 AGENT_NOT_FOUND."""
    if deps.storage_mode_of(request) == "local":
        row = await asyncio.to_thread(state.local_db.get, "agents", agent_id, user_id)
        if row is None:
            raise AppError("AGENT_NOT_FOUND", "Agent not found", 404)
        return row
    return await owned_agent(agent_id, user_id, columns)


async def count_documents(request: Request, agent_id: str) -> int:
    if deps.storage_mode_of(request) == "local":
        return len(await asyncio.to_thread(state.local_db.select, "documents", lambda r: r.get("agent_id") == agent_id))
    res = await storage.cloud_run(lambda: state.supabase.table("documents").select("id", count="exact")
                           .eq("agent_id", agent_id).limit(1).execute(), "count documents")
    return int(res.count or 0)


async def check_voice_for_agent(voice_id: Optional[str], request: Request, user_id: str) -> Optional[str]:
    """None clears the voice; otherwise the voice must exist and belong to the caller."""
    if not voice_id:
        return None
    deps.check_id(voice_id, "voice_id")
    if await voice_store.lookup_voice(voice_id, deps.storage_mode_of(request), user_id) is None:
        raise AppError("VOICE_NOT_FOUND", voice_store.VOICE_NOT_FOUND_MSG, 404, {"voice_id": voice_id})
    return voice_id


async def insert_cloud_agent(row: dict):
    """Insert an agent row; tolerate a database that has not had migration 007 (disclose_ai) or 009 (tools) applied."""
    try:
        return await storage.cloud_run(lambda: state.supabase.table("agents").insert(row).execute(), "save agent")
    except AppError as e:
        reason = str((e.details or {}).get("reason", ""))
        missing = [c for c in ("disclose_ai", "tools", "template_id") if c in reason]
        if not missing:
            raise
        log.warning("agents column %s missing (apply migrations 007_agent_disclose_ai.sql, 009_agent_tools.sql and "
                    "022_agent_template_id.sql)", ", ".join(missing))
        legacy = {k: v for k, v in row.items() if k not in missing}
        return await storage.cloud_run(lambda: state.supabase.table("agents").insert(legacy).execute(), "save agent")
