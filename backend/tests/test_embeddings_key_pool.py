"""The Embeddings key pool (services/embeddings.py) and how chat and embeddings use it: the pool is EMBEDDINGS_API_KEY then
EMBEDDINGS_API_KEYS; a key that hits its quota or is invalid is skipped and the next one answers the same request at once;
nothing logs a full key. The HTTP layer is a local fake server (chat) or a replaced _http_post (embeddings)."""
import logging

import numpy as np
import pytest

from services import embeddings, llm, retrieval
from llm_env import FakeServer, MSGS, reply, use_endpoint

A, B, C = "AAAA-key-aaaa1111", "BBBB-key-bbbb2222", "CCCC-key-cccc3333"
QUOTA = b'{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota"}}'
BAD = b'{"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}}'


@pytest.fixture()
def pool(monkeypatch):
    use_endpoint(monkeypatch, key=None)
    monkeypatch.setenv("EMBEDDINGS_API_KEY", A)
    monkeypatch.setenv("EMBEDDINGS_API_KEYS", f"{B}, {C}")
    sleeps = []
    monkeypatch.setattr(llm, "_sleep", sleeps.append)
    embeddings._reset_for_tests()
    return sleeps


@pytest.fixture()
def server(pool):
    servers = []

    def make(responses):
        s = FakeServer(responses)
        servers.append(s)
        import os
        os.environ["LLM_BASE_URL"] = s.url
        return s
    yield make
    for s in servers:
        s.close()


def test_pool_parsing_dedup_and_order(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "k1")
    monkeypatch.setenv("EMBEDDINGS_API_KEYS", "k2,k1 k3\nk2\n,k4")
    assert embeddings.keys() == ["k1", "k2", "k3", "k4"]
    monkeypatch.delenv("EMBEDDINGS_API_KEY")
    assert embeddings.keys() == ["k2", "k1", "k3", "k4"]


def test_a_single_embeddings_api_key_works_as_before(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "only")
    assert embeddings.keys() == ["only"] and embeddings.api_key() == "only" and embeddings.embed_key() == "only"
    assert embeddings.llm_configured() and embeddings.embeddings_configured()
    embeddings.report_failure("only", 429)
    assert embeddings.api_key() == "only"        # never empty while a key exists


def test_llm_api_key_wins_and_is_not_rotated(pool, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    assert embeddings.api_key() == "llm-key"
    assert llm.status()["keys"] == {"keys": 1, "available": 1}


def test_the_first_key_is_used_while_it_is_healthy(server):
    s = server([(200, reply())])
    llm._send(llm._url(), {"x": 1}, llm._headers(False), 5)
    llm._send(llm._url(), {"x": 1}, llm._headers(False), 5)
    assert [r["auth"] for r in s.requests] == [f"Bearer {A}"] * 2


def test_a_429_moves_the_same_request_to_the_next_key_without_sleeping(server, pool):
    s = server([(429, QUOTA), (200, reply())])
    data = llm._send(llm._url(), {"x": 1}, llm._headers(False), 5)
    assert data["choices"]
    assert [r["auth"] for r in s.requests] == [f"Bearer {A}", f"Bearer {B}"]
    assert pool == []
    assert embeddings.pool_status() == {"keys": 3, "available": 2}


def test_a_401_moves_on_and_the_bad_key_stays_skipped(server):
    s = server([(401, b'{"error": "unauthorized"}'), (200, reply())])
    llm._send(llm._url(), {"x": 1}, llm._headers(False), 5)
    llm._send(llm._url(), {"x": 1}, llm._headers(False), 5)
    assert [r["auth"] for r in s.requests] == [f"Bearer {A}", f"Bearer {B}", f"Bearer {B}"]


def test_a_400_that_names_a_bad_key_moves_on(server):
    s = server([(400, BAD), (200, reply())])
    llm._send(llm._url(), {"x": 1}, llm._headers(False), 5)
    assert [r["auth"] for r in s.requests] == [f"Bearer {A}", f"Bearer {B}"]


def test_a_streamed_request_switches_keys_too(server):
    s = server([(429, QUOTA), (200, b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\ndata: [DONE]\n\n')])
    resp = llm._post_stream(llm._url(), {"x": 1}, llm._headers(True), 5)
    resp.close()
    assert [r["auth"] for r in s.requests] == [f"Bearer {A}", f"Bearer {B}"]


def test_all_keys_failing_ends_in_the_normal_error(server, monkeypatch):
    monkeypatch.setenv("LLM_RETRIES", "1")
    s = server([(429, QUOTA)])
    with pytest.raises(llm.ProviderHTTPError) as e:
        llm._send(llm._url(), {"x": 1}, llm._headers(False), 5)
    assert e.value.code == 429
    assert embeddings.pool_status()["available"] == 0
    assert embeddings.api_key() in (A, B, C)     # still a key to try, never ""
    # three key switches are capped at the pool size, then the usual retry (one, with a wait) and the raise
    assert len(s.requests) <= 3 + 2


def test_all_keys_cooling_still_retries_a_short_429_like_before(server, pool, monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_API_KEYS", "")
    monkeypatch.setenv("EMBEDDINGS_API_KEY", A)
    s = server([(429, b'{"retryDelay": "1s"}'), (200, reply())])
    assert llm._send(llm._url(), {"x": 1}, llm._headers(False), 5)["choices"]
    assert pool == [1.0] and len(s.requests) == 2


def test_the_cooldown_comes_from_the_error_body(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "k1")
    monkeypatch.setenv("EMBEDDINGS_API_KEYS", "k2")
    now = [1000.0]
    monkeypatch.setattr(embeddings, "_clock", lambda: now[0])
    embeddings.report_failure("k1", 429, '"retryDelay": "30s"')
    assert embeddings.pick_key() == "k2"
    now[0] += 31
    embeddings.report_failure("k2", 429, "Quota exceeded ... PerDay")
    assert embeddings.pick_key() == "k1"          # k1's 30 s are over
    embeddings.report_failure("k1", 403)
    assert embeddings.pick_key() == "k1" or embeddings.pick_key() == "k2"
    assert embeddings.pool_status()["available"] == 0


def test_logs_never_hold_a_full_key(server, caplog):
    caplog.set_level(logging.DEBUG)
    server([(401, b"no"), (200, reply())])
    llm._send(llm._url(), {"x": 1}, llm._headers(False), 5)
    text = caplog.text
    assert A[-4:] in text and A not in text and B not in text and C not in text


def test_status_shows_counts_only(pool):
    embeddings.report_failure(A, 429)
    st = llm.status()
    assert st["keys"] == {"keys": 3, "available": 2}
    assert A not in str(st) and B not in str(st)


# ---------------------------------------------------------------------------------------------- embeddings

class Resp:
    def __init__(self, status, payload=None):
        self.status_code, self._p, self.headers = status, payload or {}, {}

    def json(self):
        return self._p


def test_embeddings_switch_keys_on_a_429(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDINGS_API_KEY", A)
    monkeypatch.setenv("EMBEDDINGS_API_KEYS", B)
    monkeypatch.setenv("EMBEDDINGS_EMBED_DIM", "128")
    monkeypatch.setenv("VAKYA_EMBED_CACHE_FILE", str(tmp_path / "c.sqlite"))
    embeddings._reset_for_tests()
    retrieval.reset_embedder_state()
    seen, sleeps = [], []

    def fake(url, headers, body, timeout):
        seen.append(headers["x-goog-api-key"])
        if headers["x-goog-api-key"] == A:
            return Resp(429)
        return Resp(200, {"embeddings": [{"values": list(np.ones(128))} for _ in body["requests"]]})

    monkeypatch.setattr(retrieval, "_http_post", fake)
    monkeypatch.setattr(retrieval, "_sleep", sleeps.append)
    out = retrieval.encode_texts(["hello pool"])
    assert out.shape == (1, 128)
    assert seen == [A, B] and sleeps == []
    retrieval.encode_texts(["another text"])
    assert seen[-1] == B                      # A stays skipped
    retrieval.reset_embedder_state()
