"""The embeddings key pool (services/model_config.py) and how the embedder uses it: the pool is EMBEDDINGS_API_KEY then
EMBEDDINGS_API_KEYS; a key that hits its quota or is invalid is skipped and the next one answers the same request at once;
nothing logs a full key. The language model has one key and is never rotated. The embeddings HTTP layer is a replaced _http_post."""
import logging

import numpy as np
import pytest

from services import llm, model_config, retrieval
from llm_env import use_endpoint

A, B, C = "AAAA-key-aaaa1111", "BBBB-key-bbbb2222", "CCCC-key-cccc3333"


@pytest.fixture()
def pool(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_API_KEY", A)
    monkeypatch.setenv("EMBEDDINGS_API_KEYS", f"{B}, {C}")
    monkeypatch.setenv("EMBEDDINGS_BASE_URL", "https://embeddings.example.test/v1")
    monkeypatch.setenv("EMBEDDINGS_MODEL", "embed-model")
    model_config._reset_for_tests()


def test_pool_parsing_dedup_and_order(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "k1")
    monkeypatch.setenv("EMBEDDINGS_API_KEYS", "k2,k1 k3\nk2\n,k4")
    assert model_config.keys() == ["k1", "k2", "k3", "k4"]
    monkeypatch.delenv("EMBEDDINGS_API_KEY")
    assert model_config.keys() == ["k2", "k1", "k3", "k4"]


def test_a_single_embeddings_key_works(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "only")
    assert model_config.keys() == ["only"] and model_config.embed_key() == "only"
    assert model_config.embeddings_configured() is False      # a base URL and a model are needed too
    assert "EMBEDDINGS_BASE_URL" in model_config.embeddings_missing() and "EMBEDDINGS_MODEL" in model_config.embeddings_missing()
    monkeypatch.setenv("EMBEDDINGS_BASE_URL", "https://embeddings.example.test/v1/")
    monkeypatch.setenv("EMBEDDINGS_MODEL", "m")
    assert model_config.embeddings_configured() and model_config.embed_base_url() == "https://embeddings.example.test/v1"
    model_config.report_failure("only", 429)
    assert model_config.embed_key() == "only"        # never empty while a key exists


def test_the_language_model_key_is_one_key_and_never_rotated(pool, monkeypatch):
    use_endpoint(monkeypatch, url="https://llm.example.test/v1", key="llm-key")
    assert model_config.api_key() == "llm-key"
    assert llm.status()["keys"] == {"keys": 1, "available": 1}
    assert model_config.embed_key() == A                      # the embedding pool is separate


def test_the_cooldown_comes_from_the_error_body(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "k1")
    monkeypatch.setenv("EMBEDDINGS_API_KEYS", "k2")
    now = [1000.0]
    monkeypatch.setattr(model_config, "_clock", lambda: now[0])
    model_config.report_failure("k1", 429, '"retryDelay": "30s"')
    assert model_config.pick_key() == "k2"
    now[0] += 31
    model_config.report_failure("k2", 429, "Quota exceeded ... PerDay")
    assert model_config.pick_key() == "k1"          # k1's 30 s are over
    model_config.report_failure("k1", 403)
    assert model_config.pick_key() == "k1" or model_config.pick_key() == "k2"
    assert model_config.pool_status()["available"] == 0


def test_pool_status_shows_counts_only(pool):
    model_config.report_failure(A, 429)
    assert model_config.pool_status() == {"keys": 3, "available": 2}
    assert A not in str(model_config.pool_status())


def test_logs_never_hold_a_full_key(pool, caplog):
    caplog.set_level(logging.DEBUG)
    model_config.report_failure(A, 401)
    assert A[-4:] in caplog.text and A not in caplog.text and B not in caplog.text


# ---------------------------------------------------------------------------------------------- embeddings

class Resp:
    def __init__(self, status, payload=None):
        self.status_code, self._p, self.headers = status, payload or {}, {}

    def json(self):
        return self._p


def test_embeddings_switch_keys_on_a_429(pool, monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDINGS_DIM", "128")
    monkeypatch.setenv("VAKYA_EMBED_CACHE_FILE", str(tmp_path / "c.sqlite"))
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
