"""The hosted embedder (services/retrieval.py: Embeddings batchEmbedContents over httpx, with a fake HTTP call), the BM25
keyword search that backs it up, the hybrid fusion, and the document paths with no key: a document added with no key is
stored without vectors and found by keyword, then embedded on a later search once a key exists."""
import asyncio
import hashlib

import numpy as np
import pytest

from services import retrieval
from tests.test_offline_agents import LOCAL, env, make_agent, upload_txt  # noqa: F401 - `env` is a fixture

DIM = 128


# ------------------------------------------------------------------------------------------------ fake Embeddings

def hash_vector(text: str, dim: int = DIM) -> list:
    """Deterministic bag-of-words vector (unnormalised on purpose: the embedder must normalise it itself)."""
    v = np.zeros(dim, dtype=np.float32)
    body = text.split("|", 1)[-1] if text.startswith(("title:", "task:")) else text
    for w in body.lower().replace("text:", " ").replace("query:", " ").split():
        v[int(hashlib.md5(w.strip(".,?!").encode()).hexdigest(), 16) % dim] += 3.0
    return v.tolist()


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code, self._payload, self.headers = status, payload or {}, headers or {}

    def json(self):
        return self._payload


class FakeEmbeddings:
    """Replaces retrieval._http_post: records every request and answers from a script (default: hash vectors)."""

    def __init__(self, script=None):
        self.calls, self.script = [], list(script or [])

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            if step is not None:
                return step
        reqs = body["requests"]
        dim = reqs[0]["outputDimensionality"]
        return FakeResponse(200, {"embeddings": [{"values": hash_vector(r["content"]["parts"][0]["text"], dim)}
                                                 for r in reqs]})

    @property
    def texts(self):
        return [r["content"]["parts"][0]["text"] for c in self.calls for r in c["body"]["requests"]]


@pytest.fixture()
def embeddings(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "test-key")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("EMBEDDINGS_EMBED_MODEL", "embeddings-embedding-2")
    monkeypatch.setenv("EMBEDDINGS_EMBED_DIM", str(DIM))
    monkeypatch.setenv("EMBED_DISK_CACHE", "off")
    monkeypatch.setenv("VAKYA_EMBED_CACHE_FILE", str(tmp_path / "embed_cache.sqlite"))
    fake = FakeEmbeddings()
    monkeypatch.setattr(retrieval, "_http_post", fake)
    monkeypatch.setattr(retrieval, "_sleep", lambda s: fake.calls.append({"sleep": s}))
    retrieval.reset_embedder_state()
    yield fake
    retrieval.reset_embedder_state()


@pytest.fixture()
def no_key(monkeypatch):
    for name in ("EMBEDDINGS_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EMBED_DISK_CACHE", "off")
    monkeypatch.setattr(retrieval, "_http_post", lambda *a, **k: pytest.fail("no request without a key"))
    retrieval.reset_embedder_state()
    yield
    retrieval.reset_embedder_state()


def requests_of(fake):
    return [c for c in fake.calls if "body" in c]


# ------------------------------------------------------------------------------------------------ the request

def test_request_shape_and_prefixes_for_embeddings_embedding_2(embeddings):
    out = retrieval.encode_texts(["What is the fee?"], kind="query")
    retrieval.encode_texts(["The fee is 500 rupees."], kind="passage")
    q, p = requests_of(embeddings)
    assert q["url"] == "https://generativelanguage.googleapis.com/v1beta/models/embeddings-embedding-2:batchEmbedContents"
    assert q["headers"]["x-goog-api-key"] == "test-key"
    req = q["body"]["requests"][0]
    assert req["model"] == "models/embeddings-embedding-2" and req["outputDimensionality"] == DIM
    assert req["content"]["parts"][0]["text"] == "task: search result | query: What is the fee?"
    assert "taskType" not in req
    assert p["body"]["requests"][0]["content"]["parts"][0]["text"] == "title: none | text: The fee is 500 rupees."
    assert out.shape == (1, DIM)


def test_a_passage_title_goes_into_the_prefix(embeddings):
    retrieval.encode_texts(["Open nine to five."], titles=["Clinic > Hours"])
    assert embeddings.texts == ["title: Clinic > Hours | text: Open nine to five."]


def test_embedding_001_uses_task_types_and_no_prefix(embeddings, monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_EMBED_MODEL", "embeddings-embedding-001")
    retrieval.encode_texts(["fee?"], kind="query")
    retrieval.encode_texts(["fee is 500"], kind="passage")
    q, p = requests_of(embeddings)
    assert q["url"].endswith("/models/embeddings-embedding-001:batchEmbedContents")
    assert q["body"]["requests"][0]["taskType"] == "RETRIEVAL_QUERY" and p["body"]["requests"][0]["taskType"] == "RETRIEVAL_DOCUMENT"
    assert q["body"]["requests"][0]["content"]["parts"][0]["text"] == "fee?"
    assert p["body"]["requests"][0]["content"]["parts"][0]["text"] == "fee is 500"


def test_vectors_are_unit_length_and_the_dimension_follows_the_config(embeddings, monkeypatch):
    vecs = retrieval.encode_texts(["alpha beta", "gamma"])
    assert vecs.dtype == np.float32 and vecs.shape == (2, DIM)
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-5)
    monkeypatch.setenv("EMBEDDINGS_EMBED_DIM", "256")
    assert retrieval.embedding_dim() == 256
    assert retrieval.encode_texts(["alpha beta"]).shape == (1, 256)
    assert requests_of(embeddings)[-1]["body"]["requests"][0]["outputDimensionality"] == 256


def test_a_vector_of_the_wrong_size_or_count_is_refused(embeddings):
    embeddings.script = [FakeResponse(200, {"embeddings": [{"values": [0.1, 0.2]}]})]
    with pytest.raises(retrieval.EmbeddingUnavailable, match="2-dimensional"):
        retrieval.encode_texts(["a"])
    retrieval.reset_embedder_state()
    embeddings.script = [FakeResponse(200, {"embeddings": []})]
    with pytest.raises(retrieval.EmbeddingUnavailable, match="0 vectors"):
        retrieval.encode_texts(["a"])


def test_empty_input_makes_no_request(embeddings):
    assert retrieval.encode_texts([]).shape == (0, DIM) and embeddings.calls == []


# ------------------------------------------------------------------------------------------------ cache, batches

def test_a_cache_hit_avoids_the_second_http_call(embeddings):
    first = retrieval.encode_texts(["the refund window", "shipping takes a week"])
    n = len(requests_of(embeddings))
    again = retrieval.encode_texts(["shipping takes a week", "the refund window"])
    assert len(requests_of(embeddings)) == n                                  # nothing was sent
    assert np.allclose(again[0], first[1]) and np.allclose(again[1], first[0])
    retrieval.encode_texts(["the refund window", "something new"])         # only the new text goes out
    assert embeddings.texts[-1].endswith("something new") and len(requests_of(embeddings)[-1]["body"]["requests"]) == 1


def test_the_cache_key_separates_kind_model_and_dimension(embeddings, monkeypatch):
    retrieval.encode_texts(["fee"], kind="query")
    retrieval.encode_texts(["fee"], kind="passage")
    assert len(requests_of(embeddings)) == 2
    monkeypatch.setenv("EMBEDDINGS_EMBED_DIM", "256")
    retrieval.encode_texts(["fee"], kind="query")
    assert len(requests_of(embeddings)) == 3
    assert retrieval._cache_key("m", 128, "query", "x") != retrieval._cache_key("m", 128, "passage", "x")
    assert retrieval._cache_key("m", 128, "query", "x") != retrieval._cache_key("n", 128, "query", "x")


def test_duplicate_texts_in_one_call_are_sent_once(embeddings):
    out = retrieval.encode_texts(["same", "same", "other"])
    assert len(requests_of(embeddings)[0]["body"]["requests"]) == 2 and np.allclose(out[0], out[1])


def test_more_than_the_batch_size_is_split_into_several_requests(embeddings):
    texts = [f"passage{i}x" for i in range(250)]
    out = retrieval.encode_texts(texts)
    sizes = [len(c["body"]["requests"]) for c in requests_of(embeddings)]
    assert retrieval.EMBED_BATCH_SIZE == 100 and sizes == [100, 100, 50] and out.shape == (250, DIM)
    n = len(requests_of(embeddings))
    assert np.allclose(retrieval.encode_texts([texts[137]])[0], out[137]) and len(requests_of(embeddings)) == n   # in order


def test_the_disk_cache_survives_the_memory_cache(embeddings, monkeypatch, tmp_path):
    monkeypatch.setenv("EMBED_DISK_CACHE", "on")
    monkeypatch.setenv("VAKYA_EMBED_CACHE_FILE", str(tmp_path / "c.sqlite"))
    first = retrieval.encode_texts(["the refund window is thirty days"])
    n = len(requests_of(embeddings))
    retrieval._embed_cache.clear()                                         # a restart: only the file remains
    again = retrieval.encode_texts(["the refund window is thirty days"])
    assert len(requests_of(embeddings)) == n and (tmp_path / "c.sqlite").exists()
    assert np.allclose(first, again, atol=2e-3) and abs(float(np.linalg.norm(again[0])) - 1.0) < 1e-5


def test_a_broken_disk_cache_is_ignored(embeddings, monkeypatch, tmp_path):
    monkeypatch.setenv("EMBED_DISK_CACHE", "on")
    monkeypatch.setenv("VAKYA_EMBED_CACHE_FILE", str(tmp_path))            # a directory: sqlite cannot open it
    assert retrieval.encode_texts(["still works"]).shape == (1, DIM)


# ------------------------------------------------------------------------------------------------ errors and retries

def test_429_and_5xx_are_retried_with_growing_backoff(embeddings):
    ok = None
    embeddings.script = [FakeResponse(429, headers={"retry-after": "0"}), FakeResponse(503), ok]
    out = retrieval.encode_texts(["hello there"])
    assert out.shape == (1, DIM) and len(requests_of(embeddings)) == 3
    sleeps = [c["sleep"] for c in embeddings.calls if "sleep" in c]
    assert len(sleeps) == 2 and sleeps[0] == retrieval.EMBED_BACKOFF_BASE_S and sleeps[1] > sleeps[0]


def test_retry_after_is_respected(embeddings):
    embeddings.script = [FakeResponse(429, headers={"retry-after": "3"}), None]
    retrieval.encode_texts(["hello"])
    assert [c["sleep"] for c in embeddings.calls if "sleep" in c] == [3.0]


def test_a_timeout_is_retried_then_gives_up_and_cools_down(embeddings):
    import httpx
    embeddings.script = [httpx.ReadTimeout("slow")] * retrieval.EMBED_MAX_ATTEMPTS
    with pytest.raises(retrieval.EmbeddingUnavailable, match="ReadTimeout"):
        retrieval.encode_texts(["one"])
    assert len(requests_of(embeddings)) == retrieval.EMBED_MAX_ATTEMPTS
    assert retrieval.embedder_status()["mode"] == "keyword" and "unreachable" in retrieval.embedder_status()["reason"]
    n = len(requests_of(embeddings))
    with pytest.raises(retrieval.EmbeddingUnavailable):                    # cooling down: no request at all
        retrieval.encode_texts(["two"])
    assert len(requests_of(embeddings)) == n


def test_the_cool_down_ends(embeddings, monkeypatch):
    embeddings.script = [FakeResponse(500)] * retrieval.EMBED_MAX_ATTEMPTS
    with pytest.raises(retrieval.EmbeddingUnavailable):
        retrieval.encode_texts(["x"])
    monkeypatch.setattr(retrieval, "_embed_down_until", 0.0)
    assert retrieval.encode_texts(["x"]).shape == (1, DIM) and retrieval.embedder_status()["mode"] == "hybrid"


def test_a_question_waiting_on_the_line_gets_fewer_tries(embeddings):
    embeddings.script = [FakeResponse(503)] * 10
    with pytest.raises(retrieval.EmbeddingUnavailable):
        retrieval.encode_query("what is the fee")
    assert len(requests_of(embeddings)) == retrieval.EMBED_QUERY_ATTEMPTS
    assert requests_of(embeddings)[0]["timeout"] == retrieval.EMBED_QUERY_TIMEOUT_S


def test_a_client_error_is_not_retried(embeddings):
    embeddings.script = [FakeResponse(400, {"error": {"message": "API key not valid"}})]
    with pytest.raises(retrieval.EmbeddingUnavailable, match="API key not valid"):
        retrieval.encode_texts(["x"])
    assert len(requests_of(embeddings)) == 1


def test_batches_that_arrived_before_a_failure_are_kept(embeddings, monkeypatch):
    texts = [f"text {i}" for i in range(150)]
    embeddings.script = [None] + [FakeResponse(500)] * retrieval.EMBED_MAX_ATTEMPTS
    with pytest.raises(retrieval.EmbeddingUnavailable):
        retrieval.encode_texts(texts)
    sent = len(requests_of(embeddings))
    monkeypatch.setattr(retrieval, "_embed_down_until", 0.0)
    retrieval.encode_texts(texts[:100])                                    # the first batch is cached
    assert len(requests_of(embeddings)) == sent


def test_no_key_means_keyword_mode_and_no_request(no_key):
    status = retrieval.embedder_status()
    assert status == {"provider": "embeddings", "model": "embeddings-embedding-2", "dim": 768, "configured": False,
                      "reason": "Needs a Embeddings key", "mode": "keyword"}
    with pytest.raises(retrieval.EmbeddingUnavailable, match="Needs a Embeddings key"):
        retrieval.encode_texts(["x"])
    assert retrieval.embeddings_available() is False


def test_status_with_a_key(embeddings):
    assert retrieval.embedder_status() == {"provider": "embeddings", "model": "embeddings-embedding-2", "dim": DIM,
                                           "configured": True, "reason": None, "mode": "hybrid"}


def test_the_llm_key_serves_embeddings_when_the_llm_is_embeddings(monkeypatch):
    monkeypatch.delenv("EMBEDDINGS_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    assert retrieval.embeddings_configured() is True


# ------------------------------------------------------------------------------------------------ BM25 keyword search

def keyword_rank(query, texts):
    info = {}
    ranked = retrieval.rank_rows(query, [{"chunk_text": t} for t in texts], [None] * len(texts), info=info)
    return [e["index"] for e in ranked], info


def test_bm25_ranks_english_passages_with_no_vectors():
    texts = ["The clinic opens at nine and closes at five.", "Parking is free for patients.",
             "Dr Rao sees patients on Monday and Thursday.", "The pharmacy closes at eight."]
    order, info = keyword_rank("when does the clinic open", texts)
    assert order[0] == 0 and info["mode"] == "keyword" and info["signals"] == ["bm25"]
    order, _ = keyword_rank("which days does Dr Rao see patients", texts)
    assert order[0] == 2
    assert keyword_rank("zzz qqq", texts)[0] == []                          # nothing shared: honestly nothing


def test_bm25_ranks_devanagari_passages():
    texts = ["क्लिनिक सुबह नौ बजे खुलता है और शाम पाँच बजे बंद होता है।", "मरीजों के लिए पार्किंग मुफ्त है।",
             "डॉक्टर राव सोमवार और गुरुवार को मरीज देखते हैं।", "फार्मेसी रात आठ बजे बंद होती है।"]
    assert keyword_rank("पार्किंग मुफ्त है क्या", texts)[0][0] == 1
    assert keyword_rank("डॉक्टर राव कब देखते हैं", texts)[0][0] == 2
    assert keyword_rank("फार्मेसी कब बंद होती है", texts)[0][0] == 3


def test_devanagari_variants_nukta_chandrabindu_and_roman_spelling_match():
    texts = ["फ़ीस पाँच सौ रुपये है।", "आँगन में पार्किंग है", "अन्य जानकारी नहीं है"]
    assert keyword_rank("फीस", texts)[0][0] == 0                           # with and without the nukta
    assert keyword_rank("आंगन", texts)[0][0] == 1                          # chandrabindu and anusvara
    assert keyword_rank("fees kitni hai", texts)[0][0] == 0                # a Hinglish question finds the Hindi text


def test_bm25_mixed_language_numbers_and_names():
    texts = ["Dr Mehta consultation fee is 500 rupees", "डॉक्टर मेहता की फीस 500 रुपये है", "Parking is free"]
    order, _ = keyword_rank("consultation fee 500", texts)
    assert set(order[:2]) == {0, 1}
    assert keyword_rank("मेहता", texts)[0][0] in (0, 1)


def test_keyword_confidence_comes_from_the_overlap_alone():
    rows = [{"chunk_text": "The clinic opens at nine and closes at five.", "score": 1.0, "similarity": None}]
    high = retrieval.assess_confidence(rows, "clinic opens nine", {"mode": "keyword"})
    low = retrieval.assess_confidence([{"chunk_text": "Parking is free", "score": 0.3, "similarity": None}],
                                      "clinic opens nine hours doctor fee", {"mode": "keyword"})
    assert high["level"] == "high" and high["dense"] is None and "keyword overlap" in high["reason"]
    assert low["level"] == "low"


# ------------------------------------------------------------------------------------------------ hybrid fusion

def test_hybrid_fuses_dense_and_bm25(monkeypatch):
    texts = ["alpha beta", "gamma delta", "the unique zebra word", "epsilon"]
    # dense alone prefers chunk 0, BM25 alone chunk 2; a dense score close to the best lets the exact word win
    close = [0.62, 0.60, 0.58, 0.40]
    far = [0.9, 0.8, 0.1, 0.5]
    assert retrieval.hybrid_order("zebra", texts, close)[0] == 2
    assert retrieval.hybrid_order("zebra", texts, far)[0] == 0
    ranked = retrieval.rank_rows("zebra", [{"chunk_text": t} for t in texts], close, info=(info := {}))
    assert info["mode"] == "hybrid" and info["signals"] == ["dense", "bm25"]
    assert ranked[0]["signals"]["bm25"] > 0 and ranked[0]["similarity"] == 0.58
    monkeypatch.setenv("RETRIEVAL_FUSION", "rrf")
    assert 2 in retrieval.hybrid_order("zebra", texts, far)[:2]
    monkeypatch.setenv("RETRIEVAL_WEIGHTS", "dense:0,bm25:1")
    monkeypatch.delenv("RETRIEVAL_FUSION")
    assert retrieval.hybrid_order("zebra", texts, far)[0] == 2              # BM25 weight only


def test_rrf_with_only_bm25_still_ranks(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_FUSION", "rrf")
    order, info = keyword_rank("zebra", ["a b", "the zebra", "zebra zebra zebra crossing"])
    assert order[0] in (1, 2) and info["fusion"] == "rrf" and info["mode"] == "keyword"


def test_hybrid_off_still_falls_back_to_bm25_without_vectors(monkeypatch):
    monkeypatch.setenv("HYBRID_SEARCH", "off")
    assert keyword_rank("zebra", ["a b", "the zebra"])[0] == [1]
    assert retrieval.hybrid_order("refund", ["refund text", "other"], [0.9, 0.1]) == [0, 1]


# ------------------------------------------------------------------------------------------------ the document paths

def run(coro):
    return asyncio.run(coro)


def search(env, agent_id, query, **kw):
    sig = {}
    out = run(env.retrieve_context(agent_id, query, user_id="local-user", storage_mode="local", signals=sig, **kw))
    return out, sig


def test_a_document_added_with_no_key_is_stored_without_vectors_and_found_by_keyword(app_client, env, no_key, monkeypatch):
    monkeypatch.setattr(env, "embedding_model", env.LazyEmbeddingModel())
    a = make_agent(app_client)
    text = "The pharmacy opens at nine every morning.\n\nParking behind the clinic is free for patients."
    assert upload_txt(app_client, a["id"], text).status_code == 200
    chunks = env.local_db.select("document_chunks")
    assert chunks and all(c["embedding"] is None and c["embed_pending"] is True and c["embed_model"] is None
                          for c in chunks)
    out, sig = search(env, a["id"], "when does the pharmacy open")
    assert out and "pharmacy" in out[0] and sig["search_mode"] == "keyword" and sig["signals_used"] == ["bm25"]
    assert sig["top_score"] is None and sig["confidence"]["level"] in ("high", "medium")


def test_hindi_document_without_a_key_is_found_in_hindi(app_client, env, no_key, monkeypatch):
    monkeypatch.setattr(env, "embedding_model", env.LazyEmbeddingModel())
    a = make_agent(app_client)
    upload_txt(app_client, a["id"], "फार्मेसी रोज सुबह नौ बजे खुलती है।\n\nक्लिनिक के पीछे पार्किंग मुफ्त है।", name="hi.txt")
    out, sig = search(env, a["id"], "पार्किंग मुफ्त है क्या")
    assert out and "पार्किंग" in out[0] and sig["search_mode"] == "keyword"


def test_search_falls_back_to_bm25_when_the_embedder_raises(app_client, env, monkeypatch):
    class Dim16:
        loaded = True

        def encode(self, texts, *a, **k):
            return np.stack([np.eye(16)[abs(hash(t)) % 16] for t in texts])
    a = make_agent(app_client)
    monkeypatch.setattr(env, "embedding_model", Dim16())
    upload_txt(app_client, a["id"], "The refund window is thirty days.\n\nShipping takes about a week.")

    class Broken:
        loaded = True

        def encode(self, texts, *a, **k):
            raise retrieval.EmbeddingUnavailable("quota exceeded")
    monkeypatch.setattr(env, "embedding_model", Broken())
    out, sig = search(env, a["id"], "how long is the refund window")
    assert out and "refund" in out[0] and sig["search_mode"] == "keyword"
    assert all(c["embedding"] is not None for c in env.local_db.select("document_chunks"))   # nothing was wiped


def test_a_failing_embedder_during_ingest_does_not_fail_the_upload(app_client, env, monkeypatch):
    class Broken:
        loaded = True

        def encode(self, texts, *a, **k):
            raise RuntimeError("socket closed")
    monkeypatch.setattr(env, "embedding_model", Broken())
    a = make_agent(app_client)
    assert upload_txt(app_client, a["id"]).status_code == 200
    assert all(c["embed_pending"] for c in env.local_db.select("document_chunks"))


def test_pending_chunks_are_embedded_by_the_next_search_once_a_key_exists(app_client, env, embeddings, monkeypatch):
    monkeypatch.setattr(env, "embedding_model", env.LazyEmbeddingModel())
    a = make_agent(app_client)
    doc = env.local_db.insert("documents", {"agent_id": a["id"], "user_id": "local-user", "filename": "old",
                                            "chunk_count": 2})
    env.local_db.insert_many("document_chunks", [
        {"document_id": doc["id"], "chunk_text": "The refund window is thirty days", "chunk_index": 0, "kind": "passage",
         "embedding": None, "embed_model": None, "embed_dim": None, "embed_pending": True},
        {"document_id": doc["id"], "chunk_text": "Shipping takes a week", "chunk_index": 1, "kind": "passage",
         "embedding": None, "embed_model": None, "embed_dim": None, "embed_pending": True}])
    out, sig = search(env, a["id"], "refund window")
    assert out[0].startswith("The refund window") and sig["search_mode"] == "hybrid" and sig["top_score"] is not None
    chunks = env.local_db.select("document_chunks")
    assert all(len(c["embedding"]) == DIM and c["embed_model"] == env.EMBEDDING_MODEL and c["embed_dim"] == DIM
               and not c["embed_pending"] for c in chunks)
    texts = embeddings.texts
    assert any(t.startswith("task: search result | query: refund window") for t in texts)
    assert any(t.startswith("title: none | text: The refund window") for t in texts)


def test_no_key_does_not_wipe_existing_embeddings(app_client, env, no_key, monkeypatch):
    monkeypatch.setattr(env, "embedding_model", env.LazyEmbeddingModel())
    a = make_agent(app_client)
    doc = env.local_db.insert("documents", {"agent_id": a["id"], "user_id": "local-user", "filename": "d", "chunk_count": 1})
    env.local_db.insert_many("document_chunks", [
        {"document_id": doc["id"], "chunk_text": "Parking is free", "chunk_index": 0, "embedding": [0.5] * 768,
         "embed_model": "an-older-model", "embed_dim": 768}])
    assert env.refresh_local_embeddings(a["id"], 768) == 0
    out, sig = search(env, a["id"], "parking")
    assert out == ["Parking is free"] and sig["search_mode"] == "keyword"
    assert env.local_db.select("document_chunks")[0]["embedding"] == [0.5] * 768
