"""Agents: create (the language model writes the persona from a role description), list, read, update and delete."""

import asyncio
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

try:
    from core import deps, state
    from core.deps import get_current_user
    from core.errors import AppError
    from core.validation import sanitize_text
    from services import agent_store, llm, llm_limits, storage
except ImportError:  # pragma: no cover
    from backend.core import deps, state
    from backend.core.deps import get_current_user
    from backend.core.errors import AppError
    from backend.core.validation import sanitize_text
    from backend.services import agent_store, llm, llm_limits, storage

log = logging.getLogger("vakya")

router = APIRouter()


class CreateAgentRequest(BaseModel):
    name: str
    role_description: str
    project_id: Optional[str] = None
    voice_id: Optional[str] = None
    disclose_ai: bool = True
    tools: Optional[List[str]] = None


class UpdateAgentRequest(BaseModel):
    name: Optional[str] = None
    system_instructions: Optional[str] = None
    greeting: Optional[str] = None
    voice_id: Optional[str] = None
    supported_languages: Optional[List[str]] = None
    disclose_ai: Optional[bool] = None
    tools: Optional[List[str]] = None
    template_id: Optional[str] = None   # the front-desk type (one of the eight template ids); null clears it


async def _generate_agent_profile(name: str, role_description: str) -> dict:
    """Persona (greeting, instructions, languages) written by the LLM for a role description."""
    system_prompt = (f"Given this role description: '{role_description}' Output ONLY valid JSON with fields: name, "
                     "greeting, system_instructions, suggested_languages (array of ISO 639-1 codes). "
                     "Do not include any text outside the JSON object.")
    try:
        res = await asyncio.to_thread(
            llm.chat,
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": f"Create the agent named '{name}' now."}],
            json_mode=True, temperature=0.3, max_tokens=700, timeout=llm_limits.llm_timeout_s(30.0))
    except llm.LLMUnavailable as e:
        raise llm_limits.llm_unavailable(e) from e
    try:
        data = llm.extract_json(res.text)
        instructions = str(data.get("system_instructions") or "").strip()
        if not instructions:
            raise ValueError("system_instructions missing")
    except Exception as e:  # noqa: BLE001
        log.warning("agent profile from %s was unusable: %s", res.provider, e)
        raise AppError("LLM_BAD_OUTPUT", "The language model did not return a usable agent. Please try again.",
                       502, {"provider": res.provider, "model": res.model}) from e
    langs = [str(x).strip().lower() for x in (data.get("suggested_languages") or []) if isinstance(x, str)]
    langs = [x for x in langs if deps.LANG_RE.match(x)][:10] or ["en"]
    return {
        "name": str(data.get("name") or "").strip()[:200] or name,
        "greeting": str(data.get("greeting") or "").strip(),
        "system_instructions": instructions[:10000],
        "supported_languages": langs,
    }


@router.get("/api/agents")
async def list_agents(request: Request, user_id: str = Depends(get_current_user)):
    if deps.storage_mode_of(request) == "local":
        rows = await asyncio.to_thread(state.local_db.select, "agents", lambda r: r.get("user_id") == user_id,
                                       order="created_at", desc=True)
        docs = await asyncio.to_thread(state.local_db.select, "documents", lambda r: r.get("user_id") == user_id)
    else:
        res = await storage.cloud_run(lambda: state.supabase.table("agents").select("*").eq("user_id", user_id)
                               .order("created_at", desc=True).execute(), "list agents")
        rows = res.data or []
        docs = []
        if rows:
            ids = [r["id"] for r in rows]
            dres = await storage.cloud_run(lambda: state.supabase.table("documents").select("agent_id")
                                    .in_("agent_id", ids).execute(), "list documents")
            docs = dres.data or []
    counts: Dict[str, int] = {}
    for d in docs:
        counts[d["agent_id"]] = counts.get(d["agent_id"], 0) + 1
    return {"agents": [agent_store.agent_json(r, counts.get(r["id"], 0)) for r in rows]}


@router.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request, user_id: str = Depends(get_current_user)):
    row = await agent_store.load_agent(request, agent_id, user_id)
    return agent_store.agent_json(row, await agent_store.count_documents(request, agent_id))


@router.post("/api/agents")
async def create_agent(body: CreateAgentRequest, request: Request, user_id: str = Depends(get_current_user)):
    name = sanitize_text(body.name, "name", 200)
    role_description = sanitize_text(body.role_description, "role_description", 4000)
    local = deps.storage_mode_of(request) == "local"
    voice_id = await agent_store.check_voice_for_agent(body.voice_id, request, user_id)
    tool_names = agent_store.check_tools(body.tools)

    if local:
        project_id = agent_store.LOCAL_PROJECT_ID
    elif body.project_id:
        owned = await storage.cloud_run(lambda: state.supabase.table("projects").select("id").eq("id", body.project_id)
                                 .eq("owner_user_id", user_id).execute(), "project lookup")
        if not owned.data:
            raise AppError("PROJECT_NOT_FOUND", "Project not found", 404)
        project_id = body.project_id
    else:
        project_id = await storage.get_or_create_project(user_id)

    profile = await _generate_agent_profile(name, role_description)
    row = {"project_id": project_id, "user_id": user_id, "voice_id": voice_id, "disclose_ai": body.disclose_ai,
           "tools": tool_names, **profile}

    if local:
        saved = await asyncio.to_thread(state.local_db.insert, "agents", row)
    else:
        row["id"] = str(uuid.uuid4())
        res = await agent_store.insert_cloud_agent(row)
        saved = (res.data or [row])[0]
    return agent_store.agent_json(saved, 0)


@router.put("/api/agents/{agent_id}")
async def update_agent(agent_id: str, body: UpdateAgentRequest, request: Request,
                       user_id: str = Depends(get_current_user)):
    sent = body.model_fields_set
    update_data: Dict[str, Any] = {}
    if "name" in sent and body.name is not None:
        update_data["name"] = sanitize_text(body.name, "name", 200)
    if "system_instructions" in sent and body.system_instructions is not None:
        update_data["system_instructions"] = sanitize_text(body.system_instructions, "system_instructions",
                                                           10000, required=False)
    if "greeting" in sent and body.greeting is not None:
        update_data["greeting"] = sanitize_text(body.greeting, "greeting", required=False)
    if "supported_languages" in sent and body.supported_languages is not None:
        if len(body.supported_languages) > 20:
            raise AppError("TEXT_TOO_LONG", "Too many supported_languages", 422, {"field": "supported_languages"})
        update_data["supported_languages"] = [deps.check_language(x) for x in body.supported_languages] or ["en"]

    if "disclose_ai" in sent and body.disclose_ai is not None:
        update_data["disclose_ai"] = bool(body.disclose_ai)
    if "tools" in sent and body.tools is not None:
        update_data["tools"] = agent_store.check_tools(body.tools)
    if "template_id" in sent:   # null clears it
        update_data["template_id"] = agent_store.check_template_id(body.template_id)

    row = await agent_store.load_agent(request, agent_id, user_id)
    if "voice_id" in sent:  # null clears the voice
        update_data["voice_id"] = await agent_store.check_voice_for_agent(body.voice_id, request, user_id)

    if update_data:
        if deps.storage_mode_of(request) == "local":
            row = await asyncio.to_thread(state.local_db.update, "agents", agent_id, update_data, user_id) or row
        else:
            await storage.cloud_run(lambda: state.supabase.table("agents").update(update_data).eq("id", agent_id)
                             .eq("user_id", user_id).execute(), "update agent")
            row = await agent_store.owned_agent(agent_id, user_id)
    return agent_store.agent_json(row, await agent_store.count_documents(request, agent_id))


def _drop_schedules(agent_id: str) -> None:
    """A deleted front desk takes its schedules and their blocks with it (the bookings stay as history)."""
    try:
        from services import schedules  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.services import schedules  # noqa: PLC0415
    try:
        for sid in schedules.ids_for_agent(agent_id):
            schedules.delete(sid)
    except Exception:  # noqa: BLE001 - never blocks deleting the agent
        log.exception("could not remove the schedules of agent %s", agent_id)


@router.delete("/api/agents/{agent_id}")
async def delete_agent(agent_id: str, request: Request, user_id: str = Depends(get_current_user)):
    await agent_store.load_agent(request, agent_id, user_id, "id")
    if deps.storage_mode_of(request) == "local":
        def purge():
            conv_ids = {c["id"] for c in state.local_db.select("conversations", lambda r: r.get("agent_id") == agent_id)}
            turns = state.local_db.select("conversation_turns", lambda r: r.get("conversation_id") in conv_ids)
            for t in turns:
                p = os.path.join(state.LOCAL_AUDIO_DIR, f"turn_{t['id']}.wav")
                if os.path.isfile(p):
                    os.unlink(p)
            state.local_db.delete_agent(agent_id, user_id)
        await asyncio.to_thread(purge)
    else:
        await storage.cloud_run(lambda: state.supabase.table("agents").delete().eq("id", agent_id).eq("user_id", user_id)
                         .execute(), "delete agent")  # documents, conversations and runs cascade
    await asyncio.to_thread(_drop_schedules, agent_id)
    return {"deleted": True}
