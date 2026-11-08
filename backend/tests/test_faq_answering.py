"""FAQ answering from an uploaded document, offline (BM25 only, no embeddings key): a 60-entry clinic FAQ in English, Hindi,
Marathi and Hinglish (tests/faq_fixture.py) and 29 paraphrased or code-mixed caller questions. Checks that the right entry comes
first, that retrieval is fast, that an entry which clearly answers is marked high-confidence with a short prompt, and that the
keyword index is built once per document set."""
import asyncio
import statistics
import time

import pytest

from core import state
from services import knowledge, retrieval
from services.local_store import LocalStore
from tests import faq_fixture as F

USER = "u1"


@pytest.fixture()
def agent(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "local_db", LocalStore(str(tmp_path / "kb")))
    monkeypatch.setenv("QUESTION_INDEX", "off")
    assert not retrieval.embeddings_configured()
    row = state.local_db.insert("agents", {"user_id": USER, "name": "Front desk"})
    doc = asyncio.run(knowledge.ingest_document(row["id"], USER, "local", "faq.txt", F.faq_text()))
    assert doc["chunk_count"] == len(F.ENTRIES) == 60          # every FAQ pair is one whole passage
    return row["id"]


def ask(agent_id, query):
    sig = {}
    out = asyncio.run(knowledge.retrieve_context(agent_id, query, user_id=USER, storage_mode="local", signals=sig))
    return out, sig


def test_the_right_entry_comes_first_for_paraphrased_and_code_mixed_questions(agent):
    first = third = 0
    misses = []
    for query, key in F.QUERIES:
        out, _ = ask(agent, query)
        accepted = F.accepted_keys(key)
        first += bool(out) and F.key_of_passage(out[0]) in accepted
        third += any(F.key_of_passage(p) in accepted for p in out[:3])
        if not (out and F.key_of_passage(out[0]) in accepted):
            misses.append((query, key, F.key_of_passage(out[0]) if out else None))
    n = len(F.QUERIES)
    assert first / n >= 0.9, (first, n, misses)         # measured 29 of 29; was 22 of 29 before the vocabulary and strong match
    assert third / n >= 0.95, (third, n)


def test_retrieval_is_fast_and_the_keyword_index_is_built_once(agent, monkeypatch):
    built = []
    real = retrieval.BM25.__init__

    def counting(self, *a, **k):
        built.append(1)
        real(self, *a, **k)
    monkeypatch.setattr(retrieval.BM25, "__init__", counting)
    retrieval._bm25_cache.clear()
    timings = []
    for _ in range(3):
        for query, _key in F.QUERIES:
            t = time.perf_counter()
            ask(agent, query)
            timings.append((time.perf_counter() - t) * 1000)
    assert len(built) == 1, len(built)                  # one index for the 60 passages, then reused every turn
    assert statistics.median(timings) < 5, statistics.median(timings)       # measured about 0.6 ms
    assert sorted(timings)[int(0.95 * len(timings)) - 1] < 20


def test_an_entry_that_clearly_answers_is_first_high_confidence_and_alone_with_one_more(agent):
    out, sig = ask(agent, "ईसीजी कितने का है")
    assert F.key_of_passage(out[0]) == "hi_ecg"
    assert sig["faq_strong"] is True and sig["confidence"]["level"] == "high" and sig["confidence"]["faq_match"] is True
    assert len(out) <= retrieval.FAQ_STRONG_KEEP < 4     # a shorter prompt than the usual four passages


def test_a_strong_match_is_never_a_wrong_one(agent):
    strong = 0
    for query, key in F.QUERIES:
        out, sig = ask(agent, query)
        if sig.get("faq_strong"):
            strong += 1
            assert F.key_of_passage(out[0]) in F.accepted_keys(key), (query, key, F.key_of_passage(out[0]))
    assert strong >= 5                                  # measured 10 of 29


@pytest.mark.parametrize("query", ["tell me about the clinic", "doctor", "what is the weather in Pune", "क्या आप मुझे बता सकते हैं", "?"])
def test_vague_questions_get_no_strong_match(agent, query):
    out, sig = ask(agent, query)
    assert not sig.get("faq_strong")
    assert len(out) <= knowledge.RETRIEVAL_TOP_K


def test_the_strong_match_can_be_switched_off(agent, monkeypatch):
    monkeypatch.setenv("FAQ_STRONG_MATCH", "off")
    out, sig = ask(agent, "ईसीजी कितने का है")
    assert not sig.get("faq_strong") and F.key_of_passage(out[0]) == "hi_ecg"


def test_faq_strong_match_only_looks_at_faq_passages():
    rows = [{"chunk_text": "The clinic opens at ten. Parking is free."}, {"chunk_text": "Q: Is parking free?\nA: Yes."}]
    assert retrieval.faq_strong_match("is parking free", rows) == 1
    assert retrieval.faq_strong_match("is parking free", rows[:1]) is None
    assert retrieval.faq_strong_match("", rows) is None
    assert retrieval.faq_strong_match("is parking free", []) is None
