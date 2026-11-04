"""Knowledge retrieval: the Hindi/English/Hinglish tokenizer, BM25, Reciprocal Rank Fusion, dimension safety, automatic
re-embedding of stale local chunks, the hosted path with a fake Supabase client (the model-neutral vector column) and the
migration SQL. Nothing is sent to Embeddings: embeddings are faked (see tests/test_offline_agents.py; the embedder itself is
tested in tests/test_retrieval_embedder.py)."""
import asyncio
from pathlib import Path

import numpy as np
import pytest

from services import retrieval
from services.local_store import LocalStore
from tests.test_offline_agents import LOCAL, env, make_agent, upload_txt  # noqa: F401 - `env` is a fixture

BACKEND = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------------------------------------ tokenizer

def test_tokenize_lowercases_and_strips_punctuation():
    assert retrieval.tokenize("What time, does the CLINIC open?!", drop_stopwords=False)[-2:] == ["clinic", "open"]
    toks = retrieval.tokenize("Clinic OPEN: Sunday!!!")
    assert toks == ["clinic", "open", "sunday"]
    assert retrieval.tokenize("") == [] and retrieval.tokenize(None) == [] and retrieval.tokenize("?!.,") == []


def test_tokenize_keeps_numbers_and_maps_devanagari_digits():
    # a number written in groups also gives its digits joined (a caller may say it without the separators)
    assert retrieval.tokenize("call 108 or 020-5550-0142") == ["cal", "108", "020", "5550", "0142", "02055500142"]
    assert retrieval.tokenize("फीस ₹४००") == retrieval.tokenize("fees Rs 400")
    assert set(retrieval.tokenize("फीस ₹४००")) == set(retrieval.tokenize("fees 400 rupees"))


@pytest.mark.parametrize("a,b", [
    ("kitnaa", "kitna"), ("kitne", "kitna"), ("kitni", "kitna"), ("paisaa", "paisa"),
    ("hai", "he"), ("hai", "hain"), ("kya", "kia"), ("nahi", "nahin"), ("nahi", "nhi"),
    ("kaise", "kese"), ("accha", "acha"), ("bahut", "bahuut"), ("fees", "fee"), ("doctors", "doctor"),
])
def test_normalise_unifies_romanised_hindi_spellings(a, b):
    assert retrieval.normalise_token(a) == retrieval.normalise_token(b)


def test_normalise_does_not_merge_unrelated_words():
    assert retrieval.normalise_token("fee") != retrieval.normalise_token("time")
    assert retrieval.normalise_token("sunday") != retrieval.normalise_token("monday")
    assert retrieval.normalise_token("108") == "108"


def test_collapse_repeats_keeps_digits():
    assert retrieval._collapse_repeats("kitnaa") == "kitna"
    assert retrieval._collapse_repeats("5500") == "5500"


def test_stopwords_dropped_in_both_scripts_but_never_everything():
    assert retrieval.tokenize("kya hai fees") == retrieval.tokenize("फीस")
    assert retrieval.tokenize("क्या है", drop_stopwords=False) == retrieval.tokenize("kya hai", drop_stopwords=False)
    assert retrieval.tokenize("what is the") != []          # only function words: kept rather than returning nothing


def test_devanagari_and_roman_forms_of_a_word_match():
    pairs = [("प्रकाश", "prakash"), ("भोजन", "bhojan"), ("कितना", "kitna"), ("फीस", "fees"), ("हूँ", "hoon")]
    for deva, roman in pairs:
        assert retrieval.tokenize(deva, drop_stopwords=False) == retrieval.tokenize(roman, drop_stopwords=False), deva


def test_devanagari_spelling_variants_match():
    # chandrabindu vs anusvara, nukta present or not
    assert retrieval.tokenize("हूँ") == retrieval.tokenize("हूं")
    assert retrieval.tokenize("ज़रूर") == retrieval.tokenize("जरूर")


# ------------------------------------------------------------------------------------------------ BM25

def test_bm25_ranks_matching_chunks_first_and_skips_non_matches():
    texts = ["The clinic is closed on public holidays", "Dentist fee is 300 rupees", "Sunday timings 10 to 1",
             "Pharmacy opens at nine"]
    assert retrieval.bm25_ranking("dentist fee", texts)[0] == 1
    assert retrieval.bm25_ranking("sunday", texts) == [2]
    assert retrieval.bm25_ranking("xylophone", texts) == []
    assert retrieval.bm25_ranking("", texts) == [] and retrieval.bm25_ranking("fee", []) == []


def test_bm25_matches_hinglish_question_to_devanagari_text():
    texts = ["पौधे जड़ों से पानी लेते हैं", "क्लोरोफिल सूर्य के प्रकाश को सोखता है", "फीस ₹499 है"]
    assert retrieval.bm25_ranking("Chlorophyll kya hai? prakash ko sokhta", texts)[0] == 1
    assert retrieval.bm25_ranking("monthly fees kitni hai 499", texts)[0] == 2


def test_bm25_prefers_rare_terms():
    bm = retrieval.BM25([["fee", "doctor", "doctor"], ["fee", "pharmacy"], ["fee", "lab"]])
    s = bm.scores(["fee", "pharmacy"])
    assert s[1] > s[0] and s[1] > s[2]


# ------------------------------------------------------------------------------------------------ fusion

def test_rrf_scores_with_k60():
    # a: ranks 1 and 3 -> 1/61 + 1/63 ; b: rank 2 and 1 -> 1/62 + 1/61 ; c: rank 3 only -> 1/63
    fused = retrieval.rrf_fuse([["a", "b", "c"], ["b", "x", "a"]])
    scores = {"a": 1 / 61 + 1 / 63, "b": 1 / 62 + 1 / 61, "x": 1 / 62, "c": 1 / 63}
    assert fused == sorted(scores, key=lambda d: -scores[d])
    assert fused[0] == "b" and retrieval.RRF_K == 60


def test_rrf_single_ranking_and_ties_and_empty():
    assert retrieval.rrf_fuse([[3, 1, 2]]) == [3, 1, 2]
    assert retrieval.rrf_fuse([[1, 2], [2, 1]]) == [1, 2]       # exact tie: the first ranking (dense) wins
    assert retrieval.rrf_fuse([]) == [] and retrieval.rrf_fuse([[], []]) == []


def test_hybrid_order_lexical_hit_beats_weak_dense_rank(monkeypatch):
    texts = ["alpha beta", "gamma delta", "the unique zebra word", "epsilon"]
    sims = [0.9, 0.8, 0.1, 0.5]                                   # dense puts the zebra chunk last
    monkeypatch.setenv("RETRIEVAL_FUSION", "rrf")                 # rank-based: any lexical hit is pulled up
    assert retrieval.hybrid_order("zebra", texts, sims)[0] in (0, 2)
    assert 2 in retrieval.hybrid_order("zebra", texts, sims)[:2]
    # weighted (default): an exact word decides between passages that are close in meaning, but does not lift a
    # passage that is far off in meaning above clearly better ones
    monkeypatch.delenv("RETRIEVAL_FUSION")
    assert retrieval.hybrid_order("zebra", texts, [0.62, 0.60, 0.58, 0.40])[0] == 2
    assert retrieval.hybrid_order("zebra", texts, sims)[0] == 0


def test_hybrid_order_chunk_without_vector_found_by_bm25_only(monkeypatch):
    texts = ["refund policy text", "other text", "no vector but refund keyword refund"]
    order = retrieval.hybrid_order("refund", texts, [0.9, 0.1, None])
    assert set(order) == {0, 1, 2} and order.index(2) < order.index(1)
    monkeypatch.setenv("HYBRID_SEARCH", "off")
    assert retrieval.hybrid_order("refund", texts, [0.9, 0.1, None]) == [0, 1]   # dense only: the None chunk is skipped


# ------------------------------------------------------------------------------------------------ local store safety

def _store(tmp_path, chunks):
    db = LocalStore(str(tmp_path))
    doc = db.insert("documents", {"agent_id": "a1", "user_id": "u", "filename": "f", "chunk_count": len(chunks)})
    db.insert_many("document_chunks", [{"document_id": doc["id"], "chunk_index": i, **c} for i, c in enumerate(chunks)])
    return db


def test_match_chunks_never_compares_mixed_dimensions(tmp_path):
    db = _store(tmp_path, [
        {"chunk_text": "alpha 384", "embedding": [1.0, 0.0, 0.0, 0.0]},
        {"chunk_text": "beta 384", "embedding": [0.0, 1.0, 0.0, 0.0]},
        {"chunk_text": "legacy two dimensional vector", "embedding": [1.0, 0.0]},     # another model's dimension
        {"chunk_text": "no vector at all", "embedding": None},
    ])
    rows = db.match_chunks("a1", [1.0, 0.0, 0.0, 0.0], 10)                           # dense only: no query text
    assert [r["chunk_text"] for r in rows] == ["alpha 384", "beta 384"]
    assert rows[0]["similarity"] == pytest.approx(1.0)
    # with text, the chunks without a comparable vector are reachable through BM25 only, similarity stays None
    rows = db.match_chunks("a1", [1.0, 0.0, 0.0, 0.0], 10, query_text="legacy vector")
    by_text = {r["chunk_text"]: r["similarity"] for r in rows}
    assert by_text["legacy two dimensional vector"] is None and by_text["alpha 384"] == pytest.approx(1.0)
    assert rows[0]["chunk_text"] in ("alpha 384", "legacy two dimensional vector")


def test_match_chunks_other_agents_and_empty(tmp_path):
    db = _store(tmp_path, [{"chunk_text": "x", "embedding": [1.0, 0.0]}])
    assert db.match_chunks("someone-else", [1.0, 0.0], 3, query_text="x") == []
    assert db.match_chunks("a1", [0.0, 1.0], 0, query_text="x") == []


def test_update_many_is_one_write(tmp_path):
    db = _store(tmp_path, [{"chunk_text": "a", "embedding": [1.0]}, {"chunk_text": "b", "embedding": [1.0]}])
    ids = [r["id"] for r in db.select("document_chunks")]
    assert db.update_many("document_chunks", {ids[0]: {"embed_model": "m1"}, "missing": {"x": 1}}) == 1
    assert {r["embed_model"] for r in db.select("document_chunks") if r.get("embed_model")} == {"m1"}
    assert db.update_many("document_chunks", {}) == 0


# ------------------------------------------------------------------------------------------------ backend: ingest, re-embed

class Dim:
    """A fake embedding model of a chosen dimension (hash bag of words, like tests/test_offline_agents.py)."""
    loaded = True

    def __init__(self, dim):
        self.dim, self.calls = dim, 0

    def encode(self, texts, *a, **k):
        import hashlib
        self.calls += 1
        out = []
        for t in texts:
            v = np.zeros(self.dim, dtype=np.float32)
            for w in t.lower().split():
                v[int(hashlib.md5(w.strip(".,?!").encode()).hexdigest(), 16) % self.dim] += 1.0
            out.append(v)
        return np.stack(out)


def run(coro):
    return asyncio.run(coro)


def test_ingest_stores_model_and_dimension(app_client, env, monkeypatch):
    monkeypatch.setattr(env, "embedding_model", Dim(16))
    a = make_agent(app_client)
    assert upload_txt(app_client, a["id"]).status_code == 200
    chunks = env.local_db.select("document_chunks")
    assert chunks and all(c["embed_model"] == env.EMBEDDING_MODEL and c["embed_dim"] == 16 == len(c["embedding"])
                          for c in chunks)


def test_stale_chunks_are_reembedded_on_model_or_dimension_change(app_client, env, monkeypatch):
    a = make_agent(app_client)
    monkeypatch.setattr(env, "embedding_model", Dim(16))
    upload_txt(app_client, a["id"], "The refund window is thirty days. Shipping takes a week.")
    old = env.local_db.select("document_chunks")
    assert {len(c["embedding"]) for c in old} == {16}

    # the model changes (different id and dimension): the next search re-embeds, then finds the chunk
    new_model = Dim(32)
    monkeypatch.setattr(env, "embedding_model", new_model)
    monkeypatch.setattr(env, "EMBEDDING_MODEL", "other/model-32")
    chunks = run(env.retrieve_context(a["id"], "refund window", user_id="local-user", storage_mode="local"))
    assert chunks and "refund" in chunks[0]
    fresh = env.local_db.select("document_chunks")
    assert {(c["embed_model"], c["embed_dim"], len(c["embedding"])) for c in fresh} == {("other/model-32", 32, 32)}
    calls = new_model.calls
    run(env.retrieve_context(a["id"], "refund window", user_id="local-user", storage_mode="local"))
    assert new_model.calls == calls + 1                      # only the query: nothing is stale any more


def test_same_model_name_but_wrong_dimension_is_still_stale(app_client, env, monkeypatch):
    a = make_agent(app_client)
    monkeypatch.setattr(env, "embedding_model", Dim(16))
    upload_txt(app_client, a["id"], "Refunds take thirty days.")
    cid = env.local_db.select("document_chunks")[0]["id"]
    env.local_db.update("document_chunks", cid, {"embedding": [0.5] * 8, "embed_dim": 8})   # tampered / half-migrated
    assert env.refresh_local_embeddings(a["id"], 16) == 1
    assert len(env.local_db.select("document_chunks")[0]["embedding"]) == 16
    assert env.refresh_local_embeddings(a["id"], 16) == 0


def test_legacy_chunks_without_model_or_vector_are_reembedded(app_client, env, monkeypatch):
    a = make_agent(app_client)
    monkeypatch.setattr(env, "embedding_model", Dim(16))
    doc = env.local_db.insert("documents", {"agent_id": a["id"], "user_id": "local-user", "filename": "old",
                                            "chunk_count": 2})
    env.local_db.insert_many("document_chunks", [
        {"document_id": doc["id"], "chunk_text": "refund in thirty days", "chunk_index": 0,
         "embedding": [0.1] * 384},                                                   # an older model's row: no embed_model
        {"document_id": doc["id"], "chunk_text": "no vector chunk about shipping", "chunk_index": 1}])
    chunks = run(env.retrieve_context(a["id"], "shipping", user_id="local-user", storage_mode="local"))
    assert chunks[0] == "no vector chunk about shipping"
    assert {len(c["embedding"]) for c in env.local_db.select("document_chunks")} == {16}


def test_retrieval_is_hybrid_and_keeps_the_top_k_shape(app_client, env, monkeypatch):
    class Random(Dim):                         # the dense side knows nothing: only BM25 can find the answer
        def encode(self, texts, *a, **k):
            return np.stack([np.random.default_rng(abs(hash(t)) % 1000).normal(size=16) for t in texts])
    monkeypatch.setattr(env, "embedding_model", Random(16))
    monkeypatch.setenv("RETRIEVAL_FUSION", "rrf")   # random vectors are confidently wrong: rank fusion lets BM25 win
    a = make_agent(app_client)
    text = ("\n".join(f"Section {i} " + " ".join(f"filler{i}_{j}" for j in range(400)) for i in range(6))
            + "\n" + "The unique pharmacy hours are nine to seven. " * 3)
    assert upload_txt(app_client, a["id"], text).status_code == 200
    out = run(env.retrieve_context(a["id"], "pharmacy hours", top_k=2, user_id="local-user", storage_mode="local"))
    assert isinstance(out, list) and len(out) <= 2 and all(isinstance(x, str) for x in out)
    assert any("pharmacy" in c for c in out)


# ------------------------------------------------------------------------------------------------ hosted mode (fake Supabase)

class _Res:
    def __init__(self, data):
        self.data = data


class FakeSupabase:
    """Just enough of the supabase-py client: documents, document_chunks (the model-neutral `embedding_vec` column) and rpc."""

    def __init__(self, chunks):
        self.chunks = chunks            # list of dicts: id, document_id, chunk_text, embedding_vec, embed_model
        self.rpc_calls, self.updates = [], []

    def rpc(self, name, params):
        self.rpc_calls.append((name, len(params["query_embedding"]), params["match_count"]))
        col = "embedding_vec"
        q = np.asarray(params["query_embedding"], dtype=np.float32)
        rows = [c for c in self.chunks if c.get(col) is not None]
        rows.sort(key=lambda c: -float(np.dot(c[col], q)))
        data = [{"id": c["id"], "document_id": c["document_id"], "chunk_text": c["chunk_text"], "chunk_index": 0,
                 "similarity": float(np.dot(c[col], q))} for c in rows[:params["match_count"]]]
        return type("Q", (), {"execute": lambda self_: _Res(data)})()

    def table(self, name):
        return _Table(self, name)


class _Table:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.upd = db, name, [], None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def or_(self, expr):
        """`<col>.is.null,embed_model.neq.<model>`: a row matches when either holds."""
        null_col, neq = expr.split(",")
        model = neq.split(".neq.")[1]
        col = null_col.split(".is.null")[0]
        self.filters.append(lambda r: r.get(col) is None or (r.get("embed_model") is not None and r.get("embed_model") != model))
        return self

    def limit(self, n):
        self.n = n
        return self

    def update(self, fields):
        self.upd = fields
        return self

    def execute(self):
        if self.name == "documents":
            return _Res([{"id": "d1"}])
        rows = [c for c in self.db.chunks if all(f(c) for f in self.filters)]
        if self.upd is not None:
            for r in rows:
                r.update(self.upd)
            self.db.updates.append(self.upd)
            return _Res(rows)
        return _Res([{"id": r["id"], "document_id": r.get("document_id", "d1"), "chunk_text": r["chunk_text"],
                      "chunk_index": 0} for r in rows][:getattr(self, "n", 1000)])


def _cloud_env(env, monkeypatch, dim, chunks):
    monkeypatch.setenv("EMBEDDINGS_EMBED_DIM", str(dim))
    monkeypatch.setattr(env, "embedding_model", Dim(dim))
    fake = FakeSupabase(chunks)
    monkeypatch.setattr(env, "supabase", fake)
    monkeypatch.setattr(env, "_cloud_backfilled", set())
    monkeypatch.setattr(env, "_cloud_backfill_wait", {})
    monkeypatch.setattr(env, "_cloud_rows_cache", {})
    return fake


def test_cloud_target_is_the_configured_dimension_only(env, monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_EMBED_DIM", "768")
    assert env.cloud_vector_target(768) == ("embedding_vec", "match_document_chunks_vec")
    for wrong in (384, 1024):
        with pytest.raises(env.AppError) as e:
            env.cloud_vector_target(wrong)
        assert e.value.code == "EMBEDDING_DIM_UNSUPPORTED"
    monkeypatch.setenv("EMBEDDINGS_EMBED_DIM", "1536")
    assert env.cloud_vector_target(1536)[0] == "embedding_vec"


def test_cloud_match_uses_the_neutral_rpc_and_backfills_old_chunks_once(env, monkeypatch):
    dim = 256
    embed = Dim(dim)
    old = [{"id": f"c{i}", "document_id": "d1", "chunk_text": t, "embedding_vec": None, "embed_model": None}
           for i, t in enumerate(["the refund window is thirty days", "shipping takes a week", "closed on sunday"])]
    stale = {"id": "c9", "document_id": "d1", "chunk_text": "parking is free of charge", "embedding_vec": [0.1] * dim,
             "embed_model": "an-older-model"}
    fake = _cloud_env(env, monkeypatch, dim, old + [stale])
    q = embed.encode(["refund window"])[0].tolist()
    rows = run(env.cloud_match("a1", "refund window", q, 2))
    assert rows[0]["chunk_text"] == "the refund window is thirty days" and len(rows) == 2
    assert all(c["embedding_vec"] is not None and len(c["embedding_vec"]) == dim for c in old + [stale])
    assert all(c["embed_model"] == env.EMBEDDING_MODEL and c["embed_dim"] == dim for c in old + [stale])
    assert {n for n, _d, _m in fake.rpc_calls} == {"match_document_chunks_vec"}
    updates = len(fake.updates)
    run(env.cloud_match("a1", "shipping", q, 2))
    assert len(fake.updates) == updates                                                 # backfill ran once


def test_cloud_match_without_a_query_vector_ranks_by_bm25(env, monkeypatch):
    chunks = [{"id": f"c{i}", "document_id": "d1", "chunk_text": t, "embedding_vec": None}
              for i, t in enumerate(["the refund window is thirty days", "shipping takes a week", "closed on sunday"])]
    fake = _cloud_env(env, monkeypatch, 128, chunks)
    info = {}
    rows = run(env.cloud_match("a1", "how many days is the refund window", None, 2, info=info))
    assert rows[0]["chunk_text"] == "the refund window is thirty days" and rows[0]["similarity"] is None
    assert fake.rpc_calls == [] and info["mode"] == "keyword"
    chunks.append({"id": "c7", "document_id": "d1", "chunk_text": "parking is free", "embedding_vec": None})
    assert [r["chunk_text"] for r in run(env.cloud_match("a1", "parking", None, 2))] == []   # kept 10 s: not re-read
    env.forget_cloud_rows("a1")
    assert [r["chunk_text"] for r in run(env.cloud_match("a1", "parking", None, 2))] == ["parking is free"]


@pytest.mark.parametrize("fusion,weights", [("rrf", (1.0, 0.9, 0.8, 0.1)), ("weighted", (1.0, 0.9, 0.8, 0.78))])
def test_cloud_hybrid_pulls_a_lexical_match_up_from_the_dense_shortlist(env, monkeypatch, fusion, weights):
    dim = 128
    vecs = np.eye(dim, dtype=np.float32)
    chunks = [{"id": f"c{i}", "document_id": "d1", "chunk_text": t, "embedding_vec": vecs[i].tolist(),
               "embed_model": None}
              for i, t in enumerate(["alpha beta", "gamma delta", "epsilon zeta", "the pharmacy opens at nine"])]
    fake = _cloud_env(env, monkeypatch, dim, chunks)
    monkeypatch.setattr(env, "EMBEDDING_MODEL", "m")
    for c in chunks:
        c["embed_model"] = "m"
    monkeypatch.setenv("RETRIEVAL_FUSION", fusion)
    q = sum(vecs[i] * w for i, w in enumerate(weights)).tolist()                    # dense ranks the pharmacy chunk last
    rows = run(env.cloud_match("a1", "pharmacy", q, 2))
    assert "the pharmacy opens at nine" in [r["chunk_text"] for r in rows]
    assert fake.rpc_calls[0][2] == 30                                                # the dense shortlist size


def test_ingest_cloud_writes_the_neutral_column(env, monkeypatch):
    inserted = {}

    class Sb:
        def table(self, name):
            class T:
                def insert(self, rows):
                    inserted[name] = rows
                    return self

                def execute(self):
                    return _Res([{"id": "doc1", "filename": "f", "created_at": "now"}])
            return T()

    monkeypatch.setenv("EMBEDDINGS_EMBED_DIM", "256")
    monkeypatch.setattr(env, "supabase", Sb())
    monkeypatch.setattr(env, "embedding_model", Dim(256))
    run(env.ingest_document("a1", "u1", "cloud", "f.txt", "hello world"))
    row = inserted["document_chunks"][0]
    assert len(row["embedding_vec"]) == 256 and row["embed_dim"] == 256 and row["embed_model"] == env.EMBEDDING_MODEL
    assert "embedding" not in row and "embedding_1024" not in row


def test_ingest_cloud_with_no_embedder_stores_chunks_without_vectors(env, monkeypatch):
    inserted = {}

    class Sb:
        def table(self, name):
            class T:
                def insert(self, rows):
                    inserted[name] = rows
                    return self

                def execute(self):
                    return _Res([{"id": "doc1", "filename": "f", "created_at": "now"}])
            return T()

    class Broken:
        def encode(self, texts, *a, **k):
            raise retrieval.EmbeddingUnavailable("Needs a Embeddings key")

    monkeypatch.setattr(env, "supabase", Sb())
    monkeypatch.setattr(env, "embedding_model", Broken())
    run(env.ingest_document("a1", "u1", "cloud", "f.txt", "hello world"))
    row = inserted["document_chunks"][0]
    assert "embedding_vec" not in row and "embed_model" not in row and row["chunk_text"]


# ------------------------------------------------------------------------------------------------ migrations + docs

def test_migration_023_adds_a_neutral_column_and_function_additively():
    low = (BACKEND / "supabase" / "migrations" / "023_embeddings_model_neutral.sql").read_text().lower()
    assert "embedding_vec vector(768)" in low and "add column if not exists" in low
    assert "embed_model text" in low and "embed_dim integer" in low
    assert "create index if not exists document_chunks_embedding_vec_idx" in low
    assert "using hnsw (embedding_vec vector_cosine_ops)" in low
    assert "create or replace function public.match_document_chunks_vec" in low and "query_embedding vector(768)" in low
    assert "how to change the dimension" in low
    body = low.split("alter table public.document_chunks add column if not exists embedding_vec")[1]
    assert "drop table" not in body and "drop column" not in body                      # additive only


def test_migrations_are_numbered_in_order_and_documented():
    """Numbers are unique and never reused; 015, 017 and 019 were removed with the features they belonged to."""
    names = sorted(p.name for p in (BACKEND / "supabase" / "migrations").glob("*.sql"))
    numbers = [n[:3] for n in names]
    assert len(set(numbers)) == len(numbers) and all(n.isdigit() for n in numbers) and numbers[:15] == [
        f"{i:03d}" for i in range(15)]
    readme = (BACKEND / "supabase" / "README.md").read_text()
    for name in ("011_embeddings_1024.sql", "023_embeddings_model_neutral.sql"):
        assert name in readme
    assert "match_document_chunks_vec" in readme and "embedding_vec" in readme and "EMBEDDINGS_EMBED_DIM" in readme
