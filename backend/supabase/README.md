# Supabase setup

Supabase is optional. In local mode (the default after `./vakya setup`) everything is stored on your computer in
`backend/data` and none of this is needed. Use Supabase for hosted mode, where several users share one deployment.

## Migrations

Run the files in `migrations/` **in order, 000 to 023**, on a fresh Supabase project (the numbers 015, 017 and 019 do
not exist). Nothing else needs to be written by hand. Either paste each file into the
Supabase SQL editor, or from `backend/` run `supabase db push` (Supabase CLI, linked to your project).

| File | What it does |
| --- | --- |
| `000_core_schema.sql` | `vector` extension, all core tables (`projects`, `voices`, `agents`, `documents`, `document_chunks` with `vector(384)` + HNSW index, `conversations`, `conversation_turns`, `evaluation_runs`, `generations`, `api_keys`), the `match_document_chunks` RPC, and the private `audio-assets` storage bucket |
| `001_rls.sql` | Owner-only RLS for `generations`, `embeddings`, `api_keys` |
| `002_agents_user_scope.sql` | Per-user ownership + RLS for agents, conversations, documents and their child tables |
| `003_projects_voices_rls.sql` | RLS for `projects` and `voices` |
| `004_consents.sql` | `consents` table and `voices.consent_id` |
| `005_turn_metrics.sql` | Per-turn timings, engine and model on `conversation_turns` (the reply-time panel reads these) |
| `006_consent_challenge.sql` | `consents.challenge_id` (the one-time sentence the recording answered) |
| `007_agent_disclose_ai.sql` | `agents.disclose_ai`: agents say they are an AI on the first reply (on by default) |
| `008_receipts.sql` | `receipts`: one signed provenance receipt per generation and agent reply, read by the public `/verify` page through the backend |
| `009_agent_tools.sql` | `agents.tools` (MCP tool servers an agent may use) and `conversation_turns.tool_calls` |
| `011_embeddings_1024.sql` | `document_chunks.embedding_1024 vector(1024)` + `embed_model`, an HNSW index on it and the `match_document_chunks_1024` RPC. Superseded by `023`; the backend no longer writes this column |
| `020_match_question_rows.sql` | Both match RPCs also return `kind`, `parent_chunk_id` and `header` (columns from `018`, which must run first) plus the parent passage of every question row they return: the backend scores question rows but hands the model the parent passage. The backend also works with the older RPCs (every row is then a passage) |
| `021_caller_profiles.sql` | `caller_profiles`: one profile per (owner, phone number) so the phone agent remembers a returning caller (name, language, last booking, follow-ups); deleted by the owner or after `CALLER_PROFILE_RETENTION_DAYS`. New table only |
| `022_agent_template_id.sql` | `agents.template_id`: the front-desk type an agent was made from (null for one made from scratch) |
| `023_embeddings_model_neutral.sql` | `document_chunks.embedding_vec vector(768)` + `embed_model` + `embed_dim`, an HNSW index on it and the `match_document_chunks_vec` RPC (the same contract as `020`): the one column the backend writes. Its header says how to change the size |

All files are idempotent, so re-running them is safe. `000` must run first because later files ALTER its tables.

### Embeddings (knowledge search)

The backend embeds knowledge with Google's Gemini embeddings: `GEMINI_EMBED_MODEL` (default `gemini-embedding-2`) at
`GEMINI_EMBED_DIM` dimensions (default 768, any value from 128 to 3072), using `GEMINI_API_KEY`. Hosted mode keeps the
vectors in `document_chunks.embedding_vec` and searches them with `match_document_chunks_vec` (migration `023`), both
sized for the configured dimension; each chunk records the `embed_model` and `embed_dim` that produced its vector. The
older `embedding` (384) and `embedding_1024` columns are left alone and are not read.

**Run `023` before deploying this backend**, otherwise uploads and searches that use vectors fail in hosted mode. After
that no manual backfill is needed: the first search of an agent embeds its chunks that have no vector yet or were embedded
with another model (once per process, in bounded batches). To change the model or the dimension, follow the note at the
top of `023`, set `GEMINI_EMBED_MODEL` / `GEMINI_EMBED_DIM`, and restart; the backend refuses a dimension the column does
not have.

With no Gemini key, or when Gemini cannot be reached (quota, network), nothing breaks: documents are stored without vectors,
and a search ranks the agent's chunks by BM25 keyword match (English and Hindi/Devanagari/Hinglish), which needs no
database function. When a key is set the stored chunks are embedded on the next search.

Search is hybrid: the match function returns a dense shortlist (30 chunks) which is re-ordered by fusing it with a BM25
ranking of the same chunks (weighted score fusion; `RETRIEVAL_FUSION=rrf` for Reciprocal Rank Fusion, k=60). In local mode BM25 runs over every chunk of the agent.
`HYBRID_SEARCH=off` turns the BM25 part off (it still answers when there are no vectors).

The `audio-assets` bucket is private. `backend.py` gives clients signed, time-limited playback links
(`create_signed_url`, valid for `SIGNED_URL_EXPIRES_S` seconds, default 3600): a conversation turn's audio link is signed
when the turn is stored.

Migration numbers are never reused: `015`, `017` and `019` (webhooks, instant answers, answer audit) were removed with the
features they belonged to, so the numbering has gaps. `000` still creates a few tables the app no longer writes
(`evaluation_runs`, `generations`, `api_keys`); they are harmless and left alone.

## Backend environment variables

Copy `backend/.env.example` to `backend/.env` (the backend also reads `../frontend/.env.local`). The three Supabase variables
are required only in hosted mode; local mode runs without them.

| Variable | Needed | Purpose |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | hosted mode | Project URL, e.g. `https://your-project.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | hosted mode, secret | Backend DB and storage access (bypasses RLS, so never expose it) |
| `SUPABASE_JWT_SECRET` | hosted mode, for authenticated routes, secret | Verifies the user JWTs sent to the API |
| `GEMINI_API_KEY` | recommended, secret | The language model (Gemini Flash) and knowledge-search embeddings. Without it agents answer from keyword search only and the language model is unavailable |
| `GEMINI_EMBED_MODEL`, `GEMINI_EMBED_DIM` | optional | Embedding model (default `gemini-embedding-2`) and vector size (default 768; must match migration `023`) |
| `GNANI_API_KEY` | required for speech, secret | Gnani AI speech: Prisma (hearing), Timbre (speaking) and voice cloning |
| `SIGNED_URL_EXPIRES_S` | optional | Lifetime of signed audio links in seconds (default 3600) |

The frontend needs its own `NEXT_PUBLIC_SUPABASE_URL` and anon key in `frontend/.env.local`.
