"""The knowledge behind an agent: reading an uploaded file, cutting it into passages, embedding and storing them (local or
Supabase), the question index, and the search that finds the passages for a question (hosted embeddings fused with BM25;
BM25 alone when there is no key or the embedder fails)."""

import asyncio
import io
import logging
import os
import threading
import time
import uuid
from typing import Dict, List, Optional

import numpy as np
import pypdf
from fastapi import UploadFile

try:
    from core import state
    from core.errors import AppError
    from core.validation import read_limited, validate_pdf_upload
    from services import (
        agent_store, chunking, grounding, question_index, rechunk as rechunk_service, retrieval, storage,
    )
except ImportError:  # pragma: no cover
    from backend.core import state
    from backend.core.errors import AppError
    from backend.core.validation import read_limited, validate_pdf_upload
    from backend.services import (
        agent_store, chunking, grounding, question_index, rechunk as rechunk_service, retrieval, storage,
    )

log = logging.getLogger("vakya")

KNOWLEDGE_TEXT_EXTENSIONS = (".txt", ".md")
KNOWLEDGE_TEXT_MAX_BYTES = 2 * 1024 * 1024


def _structured_chunking_on() -> bool:
    """STRUCTURED_CHUNKING=off goes back to the line-based passages without headers (services/chunking.chunk_text)."""
    return os.getenv("STRUCTURED_CHUNKING", "on").strip().lower() not in ("off", "0", "false", "no")


def chunk_text(text: str, chunk_size: int = chunking.DEFAULT_CHUNK_WORDS,
               overlap: int = chunking.DEFAULT_OVERLAP_WORDS) -> List[str]:
    """Passages of about 120 words with 20 words of overlap, never across a heading (services/chunking.py). They used to
    be 500 words; documents ingested then keep working and `rechunk_agent_documents` splits them smaller."""
    return chunking.chunk_text(text, chunk_size, overlap)


async def extract_knowledge_text(file: UploadFile) -> str:
    """Text of an uploaded PDF, .txt or .md knowledge file."""
    name = (file.filename or "").lower()
    if name.endswith(KNOWLEDGE_TEXT_EXTENSIONS):
        data = await read_limited(file, KNOWLEDGE_TEXT_MAX_BYTES)
        try:
            full_text = data.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise AppError("INVALID_FILE_CONTENT", "Text files must be UTF-8 encoded", 415) from e
        full_text = full_text.replace("\x00", "").strip()
    elif name.endswith(".pdf"):
        pdf_bytes = await validate_pdf_upload(file)
        try:
            pdf_reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            # layout mode keeps table columns apart so the chunker can read table rows (services/chunking.py)
            full_text = await asyncio.to_thread(
                (lambda: chunking.pdf_text_from_reader(pdf_reader)) if _structured_chunking_on() else
                (lambda: "".join((page.extract_text() or "") + "\n" for page in pdf_reader.pages).strip()))
        except Exception as e:  # noqa: BLE001
            print(f"Error reading PDF: {e}")
            raise AppError("INVALID_PDF", "Failed to parse PDF.", 400)
        if not full_text:
            raise AppError("NO_TEXT_FOUND", "No text found — OCR is not supported in this version.", 400)
    else:
        raise AppError("INVALID_FILE_TYPE", "Only PDF, TXT and MD files are supported.", 415,
                       {"allowed": [".pdf", ".txt", ".md"]})
    if not full_text:
        raise AppError("NO_TEXT_FOUND", "The file contains no text.", 400)
    return full_text


# Hosted mode: one model-neutral vector column and match function (migration 023), sized for EMBEDDINGS_DIM (768 by
# default). The older per-dimension columns (`embedding`, `embedding_1024`) belong to the removed local embedder and are
# not written. Vectors of different sizes are never compared or mixed in a column: to change the dimension follow the
# note in migration 023 and set EMBEDDINGS_DIM to the new size; `cloud_vector_target` refuses any other size.
CLOUD_VECTOR_COLUMN = "embedding_vec"
CLOUD_MATCH_RPC = "match_document_chunks_vec"


def cloud_vector_targets() -> dict:
    """{dimension: (column, match function)} for the hosted store: the configured embedding dimension only."""
    return {retrieval.embedding_dim(): (CLOUD_VECTOR_COLUMN, CLOUD_MATCH_RPC)}


def cloud_vector_target(dim: int) -> tuple:
    target = cloud_vector_targets().get(dim)
    if target is None:
        raise AppError("EMBEDDING_DIM_UNSUPPORTED",
                       f"Hosted mode stores {retrieval.embedding_dim()}-dimensional embeddings (EMBEDDINGS_DIM), not {dim}",
                       500, {"model": state.EMBEDDING_MODEL, "supported": sorted(cloud_vector_targets())})
    return target


def _try_encode(texts: List[str], kind: str = "passage") -> Optional[List[List[float]]]:
    """Vectors (lists of floats, one per text) from the embedder, or None when it cannot answer (no key, quota, network).
    Never raises: callers store the text without a vector or search by keyword."""
    if not texts:
        return []
    try:
        return [np.asarray(e, dtype=np.float32).tolist() for e in state.embedding_model.encode(texts, kind=kind)]
    except retrieval.EmbeddingUnavailable as e:      # no key, quota or network: retrieval.py has logged it already
        log.debug("embeddings unavailable: %s", e)
        return None
    except Exception as e:  # noqa: BLE001 - whatever a replaced embedder raises: the caller goes on without vectors
        log.warning("embedding failed (%s): %s", type(e).__name__, e)
        return None


def _embedding_fields(vector: Optional[List[float]]) -> dict:
    """The embedding columns of a local chunk row: the vector with the model and dimension that made it, or no vector and
    `embed_pending` when it could not be embedded yet."""
    if vector is None:
        return {"embedding": None, "embed_model": None, "embed_dim": None, "embed_pending": True}
    return {"embedding": vector, "embed_model": state.EMBEDDING_MODEL, "embed_dim": len(vector)}


def _document_passages(filename: str, text: str) -> List[dict]:
    """The passages of a document, `[{"content", "header", "kind": "passage"}]`: cut along its structure (headings, FAQ
    pairs, table rows, whole sentences) with a "title > section" header each (services/chunking.chunk_document), or, with
    STRUCTURED_CHUNKING=off, the line-based passages without headers."""
    if _structured_chunking_on():
        passages = chunking.chunk_document(text, chunking.guess_title(text, filename))
        if passages:
            return passages
    return [{"content": c, "header": "", "kind": "passage"} for c in chunk_text(text)]


_cloud_structure_columns: Optional[bool] = None


def cloud_has_structure_columns() -> bool:
    """Hosted mode: do document_chunks have `kind`, `parent_chunk_id` and `header` (migration 018)? Without them passages
    are stored as before (the header is still in the embedding) and no question rows are written. A missing column is
    remembered for the process; any other failure (the network) is not."""
    global _cloud_structure_columns
    if _cloud_structure_columns is None:
        try:
            state.supabase.table("document_chunks").select("kind, parent_chunk_id, header").limit(1).execute()
            _cloud_structure_columns = True
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "column" in msg or "42703" in msg or "pgrst204" in msg or "does not exist" in msg:
                _cloud_structure_columns = False
                log.info("document_chunks has no kind/parent_chunk_id/header columns (run migration 018): "
                         "passage headers are embedded but not stored and the question index is off")
            return False
    return _cloud_structure_columns


def _question_index_state() -> str:
    """off (QUESTION_INDEX=off) | skipped (no hosted language model, or no way to embed the questions) | pending (it will
    be built after the upload)."""
    if not question_index.enabled():
        return "off"
    if not question_index.available():
        log.info("question index skipped: no hosted language model is configured and no local background model is ready")
        return "skipped"
    if isinstance(state.embedding_model, state.LazyEmbeddingModel) and not retrieval.embeddings_available():
        log.info("question index skipped: embeddings are not available (%s)", retrieval.embedder_status()["reason"])
        return "skipped"
    return "pending"


def _start_question_index(storage_mode: str, doc: dict, passages: List[dict]) -> None:
    """Queue the question index of a stored document on the background thread (services/question_index.py). `passages` are
    the stored passage rows as `{"id", "header", "content"}`. The upload has already returned by the time it runs."""
    doc_id = doc["id"]
    local = storage_mode == "local"

    def alive() -> bool:
        return state.local_db.get("documents", doc_id) is not None if local else True

    def embed(texts: List[str]):
        return state.embedding_model.encode(texts)

    def save(rows: List[dict]) -> int:
        if local:
            return state.local_db.add_question_rows(doc_id, [
                {"chunk_text": r["question"], "content": r["question"], "kind": "question",
                 "parent_chunk_id": r["parent_chunk_id"], "chunk_index": 0, "embedding": r["embedding"],
                 "embed_model": state.EMBEDDING_MODEL, "embed_dim": len(r["embedding"])} for r in rows])
        column = cloud_vector_target(len(rows[0]["embedding"]))[0]
        extra = {"embed_model": state.EMBEDDING_MODEL, "embed_dim": len(rows[0]["embedding"])}
        data = [{"document_id": doc_id, "chunk_text": r["question"], "chunk_index": 0, "kind": "question",
                 "parent_chunk_id": r["parent_chunk_id"], column: r["embedding"], **extra} for r in rows]
        state.supabase.table("document_chunks").insert(data).execute()
        return len(data)

    def progress(info: dict) -> None:
        if local:
            state.local_db.update("documents", doc_id, {"question_index": info["status"], "question_count": info["questions"]})

    def job() -> None:
        result = question_index.index_passages(passages, embed=embed, save=save, alive=alive, progress=progress)
        log.info("question index for %s: %s (%d questions)%s", doc.get("filename"), result["status"], result["questions"],
                 f" - {result['reason']}" if result.get("reason") else "")

    question_index.submit(job)


async def ingest_document(agent_id: str, user_id: str, storage_mode: str, filename: str, text: str) -> dict:
    """Chunk, embed and store one document for an agent the caller has already verified.

    Passages follow the document's structure and carry a header (title > section); what is embedded is header + newline +
    content. The question index (QUESTION_INDEX) is built afterwards in the background: the document is usable as soon as
    this returns. Returns the document dict `{id, filename, chunk_count, created_at, question_count, question_index}`
    (local and cloud)."""
    passages = _document_passages(filename, text)
    chunks = [p["content"] for p in passages]
    headers = [p.get("header") or "" for p in passages]
    # No key, or the embeddings service unreachable: the passages are stored without a vector (marked for later) and found by BM25 until
    # refresh_local_embeddings / the hosted backfill embeds them on a search made after the key is set.
    vectors = await asyncio.to_thread(_try_encode, [chunking.embedding_text(h, c) for h, c in zip(headers, chunks)])
    qi_state = _question_index_state()
    stored: List[dict] = []

    try:
        if storage_mode == "local":
            def store():
                doc = state.local_db.insert("documents", {"agent_id": agent_id, "user_id": user_id, "filename": filename,
                                                    "chunk_count": len(chunks), "question_count": 0,
                                                    "question_index": qi_state})
                rows = state.local_db.insert_many("document_chunks", [
                    {"document_id": doc["id"], "chunk_text": c, "header": h, "kind": "passage", "chunk_index": i,
                     **_embedding_fields(v)}
                    for i, (c, h, v) in enumerate(zip(chunks, headers, vectors or [None] * len(chunks)))])
                return doc, rows
            doc, rows = await asyncio.to_thread(store)
            stored = [{"id": r["id"], "header": r["header"], "content": r["chunk_text"]} for r in rows]
        else:
            doc_res = await asyncio.to_thread(lambda: state.supabase.table("documents").insert(
                {"id": str(uuid.uuid4()), "agent_id": agent_id, "user_id": user_id, "filename": filename}).execute())
            doc = doc_res.data[0]
            column = cloud_vector_target(len(vectors[0]))[0] if vectors else CLOUD_VECTOR_COLUMN
            structured = await asyncio.to_thread(cloud_has_structure_columns)      # migration 018
            chunks_data = [{"document_id": doc["id"], "chunk_text": c, "chunk_index": i,
                            **({column: v, "embed_model": state.EMBEDDING_MODEL, "embed_dim": len(v)} if v else {}),
                            **({"kind": "passage", "header": h} if structured else {})}
                           for i, (c, h, v) in enumerate(zip(chunks, headers, vectors or [None] * len(chunks)))]
            if not vectors:
                _cloud_backfilled.discard((agent_id, CLOUD_VECTOR_COLUMN))     # embed them on a later search
            try:
                res = await asyncio.to_thread(lambda: state.supabase.table("document_chunks").insert(chunks_data).execute())
            except Exception:
                await asyncio.to_thread(lambda: state.supabase.table("documents").delete().eq("id", doc["id"]).execute())
                raise
            if structured:
                ids = {r.get("chunk_index"): r.get("id") for r in (getattr(res, "data", None) or [])}
                stored = [{"id": ids[i], "header": h, "content": c}
                          for i, (c, h) in enumerate(zip(chunks, headers)) if ids.get(i)]
            else:
                qi_state = "skipped" if qi_state == "pending" else qi_state
            doc = {**doc, "question_index": qi_state}
    except Exception as e:  # noqa: BLE001
        print(f"Error inserting document to DB: {e}")
        raise AppError("DB_ERROR", "Failed to store document", 500, {"reason": str(e)[:200]})

    forget_cloud_rows(agent_id)
    if qi_state == "pending" and stored:
        _start_question_index(storage_mode, doc, stored)
    return agent_store.document_json(doc, len(chunks))


def rechunk_local_documents(agent_id: str, user_id: str) -> dict:
    """Blocking: split the agent's local documents whose passages are still the old 500 words into ~120-word passages
    (embedded again), so they stop putting thousands of tokens in every prompt. Documents already split are left alone.
    Hosted (Supabase) documents are not touched: upload them again. Returns {"documents": n, "chunks_before": a,
    "chunks_after": b}. (All agents at once: backend/scripts/rechunk_knowledge.py.)"""
    r = rechunk_service.rechunk_documents(state.local_db, _try_encode, state.EMBEDDING_MODEL,
                                          agent_id=agent_id, user_id=user_id)
    if r["failed"]:
        raise AppError("RECHUNK_FAILED", f"{r['failed']} document(s) could not be re-split; they were left unchanged.",
                       500, {"errors": r["errors"]})
    return {k: r[k] for k in ("documents", "chunks_before", "chunks_after")}


_reembed_lock = threading.Lock()
_EMBED_BATCH = retrieval.EMBED_BATCH_SIZE   # chunks per encode call when re-embedding


def _chunk_is_stale(chunk: dict, dim: Optional[int]) -> bool:
    """A local chunk must be re-embedded when it has no vector, was embedded by another model, or its vector does not
    have the current model's dimension."""
    emb = chunk.get("embedding")
    if not emb or chunk.get("embed_model") != state.EMBEDDING_MODEL:
        return True
    return dim is not None and len(emb) != dim


def _embedding_text_of(row: dict) -> str:
    """What a stored row is embedded from: header + newline + text for a passage (contract 4), the question alone for a
    question-index row (it has no header)."""
    header = None if retrieval.is_question_row(row) else row.get("header")
    return chunking.embedding_text(header, row.get("chunk_text") or "")


_LOCAL_EMBEDDINGS_CLEAN: Dict[str, tuple] = {}   # agent id -> table signature at which its chunks were last found current
_LOCAL_EMBED_RETRY: Dict[str, tuple] = {}        # agent id -> (signature, time before which embedding is not tried again)
_EMBED_RETRY_S = 30.0                            # after a failed attempt (no key, the embeddings service away) the tables are not re-read


def refresh_local_embeddings(agent_id: str, dim: Optional[int] = None) -> int:
    """Embed an agent's local chunks that have no vector or were indexed with a different embedding model or dimension.
    `dim` is the current model's vector size (the size of the query vector just computed). Returns how many chunks were
    redone. With no embeddings key (or the embeddings service unreachable) nothing is touched and nothing is wiped: the chunks keep the
    vectors they have and the search finds the rest by BM25; the next search with a key embeds them."""
    # Reading both tables back from disk on every search only to find nothing stale cost ~7 ms for a small knowledge base
    # and grows with it: a table that has not been written since it was last found clean is not read again.
    signature = (agent_id, dim, state.EMBEDDING_MODEL, state.local_db.signature("documents", "document_chunks"))
    if _LOCAL_EMBEDDINGS_CLEAN.get(agent_id) == signature:
        return 0
    wait = _LOCAL_EMBED_RETRY.get(agent_id)
    if wait and wait[0] == signature and time.monotonic() < wait[1]:
        return 0
    doc_ids = {d["id"] for d in state.local_db.select("documents", lambda r: r.get("agent_id") == agent_id)}
    with _reembed_lock:   # two searches at once must not both embed the same stale chunks
        stale = state.local_db.select("document_chunks",
                                lambda r: r.get("document_id") in doc_ids and _chunk_is_stale(r, dim))
        if not stale:
            if None not in signature[3]:
                if len(_LOCAL_EMBEDDINGS_CLEAN) >= 512:
                    _LOCAL_EMBEDDINGS_CLEAN.clear()
                _LOCAL_EMBEDDINGS_CLEAN[agent_id] = signature
            return 0
        updates = {}
        for i in range(0, len(stale), _EMBED_BATCH):
            batch = stale[i:i + _EMBED_BATCH]
            vectors = _try_encode([_embedding_text_of(c) for c in batch])   # header + passage
            if vectors is None:                      # the chunks done so far are kept; the rest wait for a later search
                if len(_LOCAL_EMBED_RETRY) >= 512:
                    _LOCAL_EMBED_RETRY.clear()
                _LOCAL_EMBED_RETRY[agent_id] = (signature, time.monotonic() + _EMBED_RETRY_S)
                break
            for c, vec in zip(batch, vectors):
                updates[c["id"]] = {"embedding": vec, "embed_model": state.EMBEDDING_MODEL, "embed_dim": len(vec),
                                    "embed_pending": False}
        if updates:
            state.local_db.update_many("document_chunks", updates)
    if updates:
        log.info("embedded %d of %d stale chunks for agent %s with %s", len(updates), len(stale), agent_id,
                 state.EMBEDDING_MODEL)
    return len(updates)


_cloud_backfilled: set = set()      # (agent_id, vector column) pairs already checked for missing vectors in this process
_cloud_backfill_wait: Dict[tuple, float] = {}   # the same pairs -> time before which embedding is not tried again (it failed)


async def _backfill_cloud_embeddings(agent_id: str, column: str) -> int:
    """Hosted mode: embed the agent's chunks that have no vector in `column` (uploaded without a key, or before the
    switch to this model) or were embedded by another model. Runs once per agent and column per process (a new upload
    without vectors asks for another pass); new uploads are embedded at ingest. Returns how many chunks were filled."""
    key = (agent_id, column)
    if key in _cloud_backfilled or time.monotonic() < _cloud_backfill_wait.get(key, 0.0):
        return 0
    docs = await storage.cloud_run(lambda: state.supabase.table("documents").select("id").eq("agent_id", agent_id).execute(),
                            "backfill: documents")
    doc_ids = [d["id"] for d in docs.data or []]
    done, complete = 0, True
    stale = f"{column}.is.null,embed_model.neq.{state.EMBEDDING_MODEL}"
    while doc_ids:
        cols = "id, chunk_text, kind, header" if await asyncio.to_thread(cloud_has_structure_columns) else "id, chunk_text"
        res = await storage.cloud_run(lambda: state.supabase.table("document_chunks").select(cols)
                               .in_("document_id", doc_ids).or_(stale).limit(_EMBED_BATCH).execute(),
                               "backfill: missing vectors")
        rows = res.data or []
        if not rows:
            break
        vectors = await asyncio.to_thread(_try_encode, [_embedding_text_of(r) for r in rows])
        if vectors is None:                  # no key, or the embeddings service away: BM25 answers meanwhile, try again in a while
            complete = False
            _cloud_backfill_wait[key] = time.monotonic() + _EMBED_RETRY_S
            break
        for r, vec in zip(rows, vectors):
            await storage.cloud_run(lambda: state.supabase.table("document_chunks")
                             .update({column: vec, "embed_model": state.EMBEDDING_MODEL, "embed_dim": len(vec)})
                             .eq("id", r["id"]).execute(), "backfill: store vector")
        done += len(rows)
        if len(rows) < _EMBED_BATCH:
            break
    if complete:
        _cloud_backfilled.add(key)
    if done:
        log.info("embedded %d existing chunks for agent %s into %s", done, agent_id, column)
    return done


CLOUD_KEYWORD_ROW_CAP = 5000       # rows read for a keyword search in hosted mode (Postgres has no Hindi-aware text search)
_CLOUD_ROWS_TTL_S = 10.0
_cloud_rows_cache: Dict[str, tuple] = {}


def forget_cloud_rows(agent_id: str) -> None:
    """Drop the keyword-search rows kept for an agent (a document was added or removed)."""
    _cloud_rows_cache.pop(agent_id, None)


async def _cloud_keyword_rows(agent_id: str) -> List[dict]:
    """Hosted mode, no query vector: the agent's passages and question rows (id, document_id, chunk_text, chunk_index and,
    with migration 018, kind / parent_chunk_id / header), for BM25 in this process. Kept 10 s per agent."""
    hit = _cloud_rows_cache.get(agent_id)
    if hit and time.monotonic() - hit[0] < _CLOUD_ROWS_TTL_S:
        return hit[1]
    docs = await storage.cloud_run(lambda: state.supabase.table("documents").select("id").eq("agent_id", agent_id).execute(),
                            "keyword search: documents")
    doc_ids = [d["id"] for d in docs.data or []]
    rows: List[dict] = []
    if doc_ids:
        cols = ("id, document_id, chunk_text, chunk_index, kind, parent_chunk_id, header"
                if await asyncio.to_thread(cloud_has_structure_columns) else "id, document_id, chunk_text, chunk_index")
        res = await storage.cloud_run(lambda: state.supabase.table("document_chunks").select(cols)
                               .in_("document_id", doc_ids).limit(CLOUD_KEYWORD_ROW_CAP).execute(),
                               "keyword search: chunks")
        rows = list(res.data or [])
    if len(_cloud_rows_cache) >= 64:
        _cloud_rows_cache.clear()
    _cloud_rows_cache[agent_id] = (time.monotonic(), rows)
    return rows


async def cloud_match(agent_id: str, query: str, query_embedding: Optional[List[float]], want: int, *,
                      info: Optional[dict] = None) -> List[dict]:
    """Hosted mode: dense top candidates from the match function, then fused with a BM25 ranking of those candidates
    (Postgres has no Hindi-aware text search, so BM25 re-orders the dense shortlist). Without a query vector (no key, or
    the embedder failed) BM25 ranks the agent's chunks alone. Question rows (migration 020 returns kind / parent_chunk_id
    / header and the parents) stand for their parent passage and are never returned; with the older match functions every
    row is a passage."""
    if not query_embedding:
        rows = await _cloud_keyword_rows(agent_id)
        ranked = await asyncio.to_thread(retrieval.rank_rows, query, rows, [None] * len(rows), info=info)
        return [{**rows[e["index"]], "similarity": None, "score": e["score"], "signals": e["signals"],
                 "via": e["via"]} for e in ranked[:want]]
    column, rpc_name = cloud_vector_target(len(query_embedding))
    await _backfill_cloud_embeddings(agent_id, column)
    pool = max(want, 30) if retrieval.hybrid_enabled() else want
    response = await asyncio.to_thread(lambda: state.supabase.rpc(rpc_name, {
        "query_embedding": query_embedding, "match_count": pool, "target_agent_id": agent_id}).execute())
    rows = list(response.data or [])
    sims = [r.get("similarity") if isinstance(r.get("similarity"), (int, float)) else None for r in rows]
    ranked = await asyncio.to_thread(retrieval.rank_rows, query, rows, sims, info=info)
    return [{**rows[e["index"]], "similarity": e["similarity"], "score": e["score"], "signals": e["signals"],
             "via": e["via"]} for e in ranked[:want]]


def summarise_retrieval(rows: List[dict], error: bool = False) -> dict:
    """What retrieval found for one turn, stored with the agent turn (`retrieval` column / local field) and read by
    the knowledge-gap report. RRF scores are rank-based and carry no absolute meaning, so the signal is the dense
    cosine of the chunks the model was given: `top_score` is the best of them (None for a keyword search, which has no
    cosine), `top_dense` that of the chunk fused ranking put first (they differ when BM25 promoted a chunk).
    `error` marks a retrieval that failed (an outage, not a gap)."""
    sims = [r.get("similarity") for r in rows if isinstance(r.get("similarity"), (int, float))]
    first = rows[0].get("similarity") if rows else None
    doc_ids: List[str] = []
    for r in rows:
        d = r.get("document_id")
        if d and str(d) not in doc_ids and len(doc_ids) < 3:
            doc_ids.append(str(d))
    out: dict = {"n_chunks": len(rows), "top_score": round(max(sims), 4) if sims else None,
                 "top_dense": round(float(first), 4) if isinstance(first, (int, float)) else None,
                 "doc_ids": doc_ids, "model": state.EMBEDDING_MODEL, "hybrid": retrieval.hybrid_enabled()}
    chunk_ids = [str(r["id"]) for r in rows if r.get("id")]
    if chunk_ids:
        out["chunk_ids"] = chunk_ids
    if error:
        out["error"] = True
    return out


# Passages handed to the model per turn (RETRIEVAL_TOP_K, default 4; a 120-word passage is about 170 tokens). The doc
# QA evaluation (docs/DOC_QA_EVAL.md) found every top-3 retrieval miss at rank 4: four passages lift hit@k from 94.7% to
# 98.2% for about 170 more prompt tokens and no added latency. The v2 grounding prompt shows the same number
# (grounding.top_k() follows this unless GROUNDING_TOP_K is set).
RETRIEVAL_TOP_K = grounding.retrieval_top_k()


async def retrieve_context(agent_id: str, query: str, top_k: int = RETRIEVAL_TOP_K, *, user_id: str,
                           storage_mode: Optional[str] = None, signals: Optional[dict] = None) -> List[str]:
    """Retrieval helper function for RAG. Raises 404 unless the agent belongs to user_id.
    `storage_mode` defaults to local when Supabase is not configured, otherwise cloud. When `signals` (a dict) is
    given it is filled with `summarise_retrieval` of what was found, plus `confidence` (retrieval.assess_confidence:
    level high / medium / low / none, contract 2), `fusion` and `search_mode`.

    Returns the passage texts, best first, each as header + newline + passage when the passage has a header
    (contract 4). Hybrid search (services/retrieval.py rank_rows): The hosted embedder's dense cosine and BM25 over the
    Hinglish/Devanagari normaliser, fused by weighted score fusion; question-index rows stand for their parent passage.
    When hosted embeddings cannot be reached (no key, a quota or network error) the same passages are ranked by BM25
    alone (`signals["search_mode"]` is then "keyword")."""
    mode = storage_mode or ("local" if state.supabase is None else "cloud")
    if mode == "local":
        if await asyncio.to_thread(state.local_db.get, "agents", agent_id, user_id) is None:
            raise AppError("AGENT_NOT_FOUND", "Agent not found", 404)
    else:
        await agent_store.owned_agent(agent_id, user_id, "id")
    # The query vector, or None when the embedder cannot answer (no key, quota, network): then BM25 ranks alone.
    query_vectors = await asyncio.to_thread(_try_encode, [query], "query")
    query_embedding = query_vectors[0] if query_vectors else None
    want = top_k
    pool = max(top_k, retrieval.FAQ_POOL) if retrieval.faq_strong_enabled() else top_k

    try:
        info: dict = {}
        if mode == "local":
            if query_embedding is not None:
                await asyncio.to_thread(refresh_local_embeddings, agent_id, len(query_embedding))
            rows = await asyncio.to_thread(state.local_db.match_chunks, agent_id, query_embedding or [], pool, query,
                                           info=info)
        else:
            rows = await cloud_match(agent_id, query, query_embedding, pool, info=info)
        strong = retrieval.faq_strong_match(query, rows) if retrieval.faq_strong_enabled() else None
        if strong is not None:     # the caller asked what a stored FAQ question asks: that entry first, a shorter prompt
            rows = [rows[strong]] + [r for i, r in enumerate(rows) if i != strong]
            top_k = min(top_k, retrieval.FAQ_STRONG_KEEP)
        confidence = retrieval.assess_confidence(rows[:top_k], query, info)
        if strong is not None:
            confidence = {**confidence, "level": "high", "faq_match": True,
                          "reason": "FAQ question matches the caller's question"}
        if signals is not None:
            signals.update(summarise_retrieval(rows[:top_k]))
            signals.update({"confidence": confidence, "fusion": info.get("fusion"), "search_mode": info.get("mode"),
                            "signals_used": info.get("signals") or [],
                            "scores": [r.get("score") for r in rows[:top_k]]})
            if strong is not None:
                signals["faq_strong"] = True
            if any(r.get("via") == "question" for r in rows[:top_k]):
                signals["via_question"] = sum(1 for r in rows[:top_k] if r.get("via") == "question")
        return [retrieval.passage_text(row) for row in rows[:top_k]]
    except Exception as e:
        print(f"Error retrieving context: {e}")
        if signals is not None:
            signals.update(summarise_retrieval([], error=True))
        return []
