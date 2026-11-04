"""A 429 or 5xx from the model is retried a couple of times with a short backoff; a long wait, another error and a
spent retry budget go to the caller as LLMUnavailable (which says its fallback line)."""
import json

import pytest

from services import llm
from llm_env import FakeServer, MSGS, chunk, reply, sse, use_endpoint


@pytest.fixture()
def server(monkeypatch):
    made = []
    sleeps = []

    def make(*responses):
        s = FakeServer(responses)
        made.append(s)
        use_endpoint(monkeypatch, s.url)
        monkeypatch.setattr(llm, "_sleep", sleeps.append)
        s.sleeps = sleeps
        return s
    yield make
    for s in made:
        s.close()


def limited(delay=None):
    err = {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}
    if delay:
        err["error"]["details"] = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": f"{delay}s"}]
    return (429, json.dumps(err).encode())


def test_a_429_is_retried_after_the_delay_the_server_names(server):
    s = server(limited(1.5), (200, reply()))
    assert llm.chat(MSGS).text == "OK"
    assert len(s.requests) == 2 and s.sleeps == [1.5]


def test_without_a_named_delay_the_wait_grows(server):
    s = server((503, b'{"error":{"message":"busy"}}'), (500, b"{}"), (200, reply()))
    assert llm.chat(MSGS).text == "OK"
    assert s.sleeps == [0.5, 1.0]


def test_a_wait_longer_than_the_cap_is_not_taken(server):
    s = server(limited(30))
    with pytest.raises(llm.LLMUnavailable) as e:
        llm.chat(MSGS)
    assert "HTTP 429" in e.value.reasons["openai-compatible"] and len(s.requests) == 1 and s.sleeps == []


def test_the_retry_budget_runs_out(server):
    s = server((500, b'{"error":"boom"}'))
    with pytest.raises(llm.LLMUnavailable):
        llm.chat(MSGS)
    assert len(s.requests) == 3                          # the first try and LLM_RETRIES (2) more
    assert "HTTP 500" in llm.last_failures["openai-compatible"]


def test_other_errors_are_not_retried(server):
    s = server((401, b'{"error":{"message":"bad key"}}'))
    with pytest.raises(llm.LLMUnavailable):
        llm.chat(MSGS)
    assert len(s.requests) == 1 and s.sleeps == []


def test_retries_can_be_turned_off(server, monkeypatch):
    s = server((429, b"{}"), (200, reply()))
    monkeypatch.setenv("LLM_RETRIES", "0")
    with pytest.raises(llm.LLMUnavailable):
        llm.chat(MSGS)
    assert len(s.requests) == 1


def test_a_stream_is_retried_before_its_first_byte(server):
    s = server((429, b'{"error":{"message":"try again in 0.4s"}}'), (200, sse(chunk("hi"), "data: [DONE]")))
    assert "".join(llm.chat_stream(MSGS)) == "hi"
    assert len(s.requests) == 2 and s.sleeps == [0.4]


def test_a_failed_request_is_remembered_until_the_next_one_succeeds(server):
    server((400, b'{"error":"bad"}'), (200, reply()))
    with pytest.raises(llm.LLMUnavailable):
        llm.chat(MSGS)
    assert "HTTP 400" in llm.last_failures["openai-compatible"]
    assert llm.chat(MSGS).text == "OK"
    assert llm.last_failures == {}


def test_the_request_names_the_client_and_the_key(server):
    s = server((200, reply()))
    llm.chat(MSGS)
    assert s.requests[0]["auth"] == "Bearer secret" and llm.USER_AGENT.startswith("Vakya/")
