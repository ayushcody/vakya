"""An agent's knowledge documents: upload (PDF, TXT, MD), re-split old passages, list and delete."""

import asyncio
import os

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

try:
    from core import deps, state
    from core.deps import get_current_user
    from core.errors import AppError
    from core.validation import sanitize_text
    from services import agent_store, knowledge, storage
except ImportError:  # pragma: no cover
    from backend.core import deps, state
    from backend.core.deps import get_current_user
    from backend.core.errors import AppError
    from backend.core.validation import sanitize_text
    from backend.services import agent_store, knowledge, storage


router = APIRouter()


@router.post("/api/knowledge/upload")
async def upload_knowledge(
    request: Request,
    agent_id: str = Form(...),
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user)
):
    await agent_store.load_agent(request, agent_id, user_id, "id")
    full_text = await knowledge.extract_knowledge_text(file)
    filename = sanitize_text(os.path.basename(file.filename or "document"), "filename", 255)
    doc = await knowledge.ingest_document(agent_id, user_id, deps.storage_mode_of(request), filename, full_text)
    return {"document": doc, "document_id": doc["id"], "chunks_created": doc["chunk_count"]}


@router.post("/api/agents/{agent_id}/knowledge/rechunk")
async def rechunk_knowledge(agent_id: str, request: Request, user_id: str = Depends(get_current_user)):
    """Split this agent's old 500-word passages into ~120-word ones (local storage only)."""
    await agent_store.load_agent(request, agent_id, user_id, "id")
    if deps.storage_mode_of(request) != "local":
        raise AppError("NOT_SUPPORTED", "Hosted documents are split when they are uploaded: upload them again.", 400)
    return await asyncio.to_thread(knowledge.rechunk_local_documents, agent_id, user_id)


@router.get("/api/agents/{agent_id}/documents")
async def list_documents(agent_id: str, request: Request, user_id: str = Depends(get_current_user)):
    await agent_store.load_agent(request, agent_id, user_id, "id")
    if deps.storage_mode_of(request) == "local":
        docs = await asyncio.to_thread(state.local_db.select, "documents", lambda r: r.get("agent_id") == agent_id,
                                       order="created_at", desc=True)
        out = [agent_store.document_json(d, d.get("chunk_count", 0)) for d in docs]
    else:
        res = await storage.cloud_run(lambda: state.supabase.table("documents").select("id, filename, created_at")
                               .eq("agent_id", agent_id).order("created_at", desc=True).execute(), "list documents")
        out = []
        structured = await asyncio.to_thread(knowledge.cloud_has_structure_columns)      # migration 018
        for d in res.data or []:
            if structured:     # question rows are not passages: count them apart
                cnt = await storage.cloud_run(lambda: state.supabase.table("document_chunks").select("id", count="exact")
                                       .eq("document_id", d["id"]).or_("kind.is.null,kind.eq.passage")
                                       .limit(1).execute(), "count chunks")
                qcnt = await storage.cloud_run(lambda: state.supabase.table("document_chunks").select("id", count="exact")
                                        .eq("document_id", d["id"]).eq("kind", "question")
                                        .limit(1).execute(), "count questions")
                out.append(agent_store.document_json(d, cnt.count or 0, qcnt.count or 0))
            else:
                cnt = await storage.cloud_run(lambda: state.supabase.table("document_chunks").select("id", count="exact")
                                       .eq("document_id", d["id"]).limit(1).execute(), "count chunks")
                out.append(agent_store.document_json(d, cnt.count or 0))
    return {"documents": out}


@router.delete("/api/agents/{agent_id}/documents/{doc_id}")
async def delete_document(agent_id: str, doc_id: str, request: Request, user_id: str = Depends(get_current_user)):
    await agent_store.load_agent(request, agent_id, user_id, "id")
    if deps.storage_mode_of(request) == "local":
        doc = await asyncio.to_thread(state.local_db.get, "documents", doc_id, user_id)
        if doc is None or doc.get("agent_id") != agent_id:
            raise AppError("DOCUMENT_NOT_FOUND", "Document not found", 404)
        await asyncio.to_thread(state.local_db.delete_document, doc_id)
    else:
        res = await storage.cloud_run(lambda: state.supabase.table("documents").select("id").eq("id", doc_id)
                               .eq("agent_id", agent_id).eq("user_id", user_id).execute(), "document lookup")
        if not res.data:
            raise AppError("DOCUMENT_NOT_FOUND", "Document not found", 404)
        await storage.cloud_run(lambda: state.supabase.table("documents").delete().eq("id", doc_id).execute(),
                         "delete document")  # chunks cascade
        knowledge.forget_cloud_rows(agent_id)
    return {"deleted": True}
