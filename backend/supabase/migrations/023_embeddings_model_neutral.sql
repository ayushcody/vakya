-- 023_embeddings_model_neutral.sql: one model-neutral vector column and match function for knowledge search, sized for the
-- configured embedding dimension (GEMINI_EMBED_DIM, 768 by default; gemini-embedding-2 and gemini-embedding-001 both
-- return a vector of any size from 128 to 3072). Idempotent; nothing existing is changed or dropped.
--
-- Why a new column: a pgvector column has a fixed size, and the older ones (`embedding` vector(384) from 000,
-- `embedding_1024` from 011) hold vectors of a model the backend no longer uses. Vectors of different models are never
-- comparable, so they are not reused: the backend writes `embedding_vec` and re-embeds an agent's chunks into it on the
-- first search after the upload or the switch (services/knowledge.py, `_backfill_cloud_embeddings`: it picks the rows whose
-- `embedding_vec` is null or whose `embed_model` differs from the configured model). With no Gemini key nothing is
-- written and nothing is lost: the backend ranks the chunks by BM25 until a key is set.
--
-- Contract of match_document_chunks_vec: the same as match_document_chunks in 020: the `match_count` closest rows
-- (passages and question rows), plus the parents of the questions among them, with kind / parent_chunk_id / header (columns
-- from 018, which must run first). Run 018 and 020 before this file.
--
-- HOW TO CHANGE THE DIMENSION (for example to 1536 or 3072): pgvector's HNSW index accepts at most 2000 dimensions for
-- `vector`, so 3072 needs `halfvec(3072)` and `halfvec_cosine_ops` instead. For any new size N:
--   1. drop index public.document_chunks_embedding_vec_idx;
--   2. alter table public.document_chunks alter column embedding_vec type vector(N) using null;   -- old vectors are dropped
--   3. update public.document_chunks set embed_model = null, embed_dim = null;                        -- (optional) mark all stale
--   4. create index document_chunks_embedding_vec_idx on public.document_chunks using hnsw (embedding_vec vector_cosine_ops);
--   5. drop function public.match_document_chunks_vec(vector, integer, uuid);
--      and create it again from this file with vector(N) in place of vector(768);
--   6. set GEMINI_EMBED_DIM=N in backend/.env and restart the backend.
-- The backend refuses to store or search with any other size (EMBEDDING_DIM_UNSUPPORTED) and re-embeds the agents' chunks
-- on their next search.

alter table public.document_chunks add column if not exists embedding_vec vector(768);
-- which model and size produced the vector in `embedding_vec`
alter table public.document_chunks add column if not exists embed_model text;
alter table public.document_chunks add column if not exists embed_dim integer;

-- HNSW, like the earlier indexes: no training data needed, correct on an empty table.
create index if not exists document_chunks_embedding_vec_idx
  on public.document_chunks using hnsw (embedding_vec vector_cosine_ops);

-- SECURITY INVOKER (default): RLS applies to non-service callers; the backend's service-role key bypasses it and checks
-- ownership itself before calling.
create or replace function public.match_document_chunks_vec(
  query_embedding vector(768),
  match_count integer,
  target_agent_id uuid
)
returns table (
  id uuid,
  document_id uuid,
  chunk_text text,
  chunk_index integer,
  similarity double precision,
  kind text,
  parent_chunk_id uuid,
  header text
)
language sql
stable
as $$
  with scored as (
    select c.id, c.document_id, c.chunk_text, c.chunk_index,
           (1 - (c.embedding_vec <=> query_embedding))::double precision as similarity,
           coalesce(c.kind, 'passage') as kind, c.parent_chunk_id, c.header
    from public.document_chunks c
    join public.documents d on d.id = c.document_id
    where d.agent_id = target_agent_id
      and c.embedding_vec is not null
    order by c.embedding_vec <=> query_embedding
    limit match_count
  )
  select * from scored
  union all
  select p.id, p.document_id, p.chunk_text, p.chunk_index,
         (1 - (p.embedding_vec <=> query_embedding))::double precision,
         coalesce(p.kind, 'passage'), p.parent_chunk_id, p.header
  from public.document_chunks p
  where p.id in (select s.parent_chunk_id from scored s where s.parent_chunk_id is not null)
    and p.id not in (select s.id from scored s);
$$;
