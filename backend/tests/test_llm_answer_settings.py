"""Generation settings of a request (services/llm.py): the answer temperature, the reasoning budget (reasoning tokens
count against max_tokens), and a server that does not take reasoning_effort."""
import json

import pytest

from services import llm
from llm_env import FakeServer, MSGS, chunk, reply, sse, use_endpoint


@pytest.fixture()
def server(monkeypatch):
    made = []

    def make(*responses, effort=None, url=True):
        s = FakeServer(responses)
        made.append(s)
        use_endpoint(monkeypatch, s.url, effort=effort)
        return s
    yield make
    for s in made:
        s.close()


def test_answer_temperature_env_is_clamped(monkeypatch):
    monkeypatch.delenv("LLM_ANSWER_TEMPERATURE", raising=False)
    assert llm.answer_temperature() == 0.2
    monkeypatch.setenv("LLM_ANSWER_TEMPERATURE", "5")
    assert llm.answer_temperature() == 1.0
    monkeypatch.setenv("LLM_ANSWER_TEMPERATURE", "-1")
    assert llm.answer_temperature() == 0.0
    monkeypatch.setenv("LLM_ANSWER_TEMPERATURE", "warm")
    assert llm.answer_temperature() == 0.2


def test_the_request_has_the_model_temperature_and_a_reply_budget(server):
    s = server((200, reply()), effort="off")
    llm.chat(MSGS, temperature=0.2, max_tokens=160)
    body = s.requests[0]["body"]
    assert body["model"] == "tiny" and body["temperature"] == 0.2 and body["max_tokens"] == 160
    assert "reasoning_effort" not in body and s.requests[0]["auth"] == "Bearer secret"


def test_reasoning_effort_and_room_for_it(server, monkeypatch):
    s = server((200, reply()), (200, sse(chunk("ok"), "data: [DONE]")), effort="low")
    monkeypatch.setenv("REASONING_TOKEN_ROOM", "500")
    llm.chat(MSGS, max_tokens=160)
    assert s.requests[0]["body"]["reasoning_effort"] == "low" and s.requests[0]["body"]["max_tokens"] == 660
    list(llm.chat_stream(MSGS, max_tokens=100))
    assert s.requests[1]["body"]["reasoning_effort"] == "low" and s.requests[1]["body"]["max_tokens"] == 600


def test_the_default_effort_is_low_on_embeddingss_endpoint_only(monkeypatch):
    monkeypatch.setattr(llm, "_effort_rejected", False)
    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    assert llm.reasoning_effort() == "low"
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    assert llm.reasoning_effort() == ""
    monkeypatch.setenv("LLM_REASONING_EFFORT", "minimal")
    assert llm.reasoning_effort() == "minimal"
    monkeypatch.setenv("LLM_REASONING_EFFORT", "off")
    assert llm.reasoning_effort() == ""


def test_a_server_that_rejects_reasoning_effort_is_asked_again_without_it(server):
    refusal = json.dumps({"error": {"message": "Unrecognized request argument: reasoning_effort (reasoning)"}}).encode()
    s = server((400, refusal), (200, reply()), effort="low")
    assert llm.chat(MSGS, max_tokens=50).text == "OK"
    assert "reasoning_effort" in s.requests[0]["body"] and "reasoning_effort" not in s.requests[1]["body"]
    llm.chat(MSGS, max_tokens=50)                  # remembered: not sent again
    assert "reasoning_effort" not in s.requests[2]["body"] and len(s.requests) == 3


def test_a_streamed_request_also_drops_a_rejected_reasoning_effort(server):
    refusal = json.dumps({"error": {"message": "reasoning not supported"}}).encode()
    s = server((400, refusal), (200, sse(chunk("ok"), "data: [DONE]")), effort="high")
    assert "".join(llm.chat_stream(MSGS)) == "ok"
    assert "reasoning_effort" not in s.requests[1]["body"]
