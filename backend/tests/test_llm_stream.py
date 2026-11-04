"""llm.chat_stream: SSE parsing against a real local HTTP server, failures before and after the first token, the total
timeout, early close, and streamed tool-call deltas (a whole call in one chunk, no index, no id, several calls)."""
import json
import time

import pytest

from services import llm
from llm_env import FakeServer, MSGS, chunk, sse, use_endpoint


@pytest.fixture()
def server(monkeypatch):
    made = []

    def make(*responses):
        s = FakeServer(responses)
        made.append(s)
        use_endpoint(monkeypatch, s.url)
        return s
    yield make
    for s in made:
        s.close()


def test_sse_parsing_deltas_usage_and_summary(server):
    body = sse(": keep-alive", {"choices": [{"delta": {"role": "assistant"}}]}, chunk("Hel"), "event: ping",
               chunk("lo"), chunk(" wor"), chunk("ld."), "data: not json", {"choices": []},
               chunk(None, usage={"total_tokens": 12, "completion_tokens": 4}), "data: [DONE]",
               chunk("ignored after done"))
    s = server((200, body))
    stream = llm.chat_stream(MSGS, temperature=0.1, max_tokens=50)
    assert stream.result is None
    deltas = list(stream)
    assert deltas == ["Hel", "lo", " wor", "ld."]
    r = stream.result
    assert r.text == "Hello world." and r.provider == "openai-compatible" and r.model == "tiny"
    assert r.usage == {"total_tokens": 12, "completion_tokens": 4}
    assert r.first_token_ms is not None and r.first_token_ms <= r.latency_ms
    req = s.requests[0]
    assert req["path"] == "/v1/chat/completions" and req["auth"] == "Bearer secret"
    assert req["body"]["stream"] is True and req["body"]["messages"] == MSGS
    assert req["body"]["stream_options"] == {"include_usage": True}
    assert llm.usage_today()["openai-compatible"]["tokens"] >= 12


def test_the_request_carries_only_the_documented_fields(server, monkeypatch):
    s = server((200, sse(chunk("ok"), "data: [DONE]")))
    monkeypatch.setenv("LLM_REASONING_EFFORT", "off")
    list(llm.chat_stream([{"role": "system", "content": "be brief"}, *MSGS], temperature=0.2, max_tokens=33))
    assert set(s.requests[0]["body"]) == {"model", "messages", "temperature", "max_tokens", "stream", "stream_options"}


def test_a_server_that_rejects_stream_options_is_asked_again_without_it(server):
    rejected = json.dumps({"error": {"message": "unknown field stream_options"}}).encode()
    s = server((400, rejected), (200, sse(chunk("ok"), {"choices": [], "usage": {"total_tokens": 3}}, "data: [DONE]")))
    stream = llm.chat_stream(MSGS)
    assert "".join(stream) == "ok" and stream.result.usage == {"total_tokens": 3}
    assert "stream_options" in s.requests[0]["body"] and "stream_options" not in s.requests[1]["body"]


def test_server_error_event_before_text_is_a_failure(server):
    server((200, sse({"error": {"message": "model overloaded"}})))
    with pytest.raises(llm.LLMUnavailable) as e:
        list(llm.chat_stream(MSGS))
    assert "model overloaded" in e.value.reasons["openai-compatible"]
    assert "model overloaded" in llm.last_failures["openai-compatible"]


def test_http_error_and_empty_reply_are_failures(server, monkeypatch):
    server((400, b'{"error":"boom"}'))
    with pytest.raises(llm.LLMUnavailable) as e:
        list(llm.chat_stream(MSGS))
    assert "HTTP 400" in e.value.reasons["openai-compatible"]
    server((200, sse({"choices": [{"delta": {"role": "assistant"}}]}, "data: [DONE]")))
    with pytest.raises(llm.LLMUnavailable) as e:
        list(llm.chat_stream(MSGS))
    assert "empty reply" in e.value.reasons["openai-compatible"]


def test_a_failure_after_the_first_token_is_an_interruption(monkeypatch):
    use_endpoint(monkeypatch, "http://127.0.0.1:9/v1")

    def broken(model, messages, **kw):
        kw["info"]["model"] = model
        yield "Half a sen"
        raise ConnectionResetError("connection reset")
    monkeypatch.setattr(llm, "_openai_stream", broken)
    stream = llm.chat_stream(MSGS)
    got = []
    with pytest.raises(llm.LLMStreamInterrupted) as e:
        for d in stream:
            got.append(d)
    assert got == ["Half a sen"] and e.value.partial == "Half a sen" and e.value.model == "tiny"
    assert e.value.provider == "openai-compatible" and stream.result is None


def test_total_timeout_interrupts_a_slow_reply(monkeypatch):
    use_endpoint(monkeypatch, "http://127.0.0.1:9/v1")

    def slow(model, messages, **kw):
        for _ in range(50):
            time.sleep(0.03)
            yield "x"
    monkeypatch.setattr(llm, "_openai_stream", slow)
    with pytest.raises(llm.LLMStreamInterrupted) as e:
        list(llm.chat_stream(MSGS, total_timeout=0.1))
    assert isinstance(e.value.cause, TimeoutError) and e.value.partial


def test_close_stops_the_request_and_releases_resources(monkeypatch):
    use_endpoint(monkeypatch, "http://127.0.0.1:9/v1")
    state = {"closed": False}

    def gen(model, messages, **kw):
        try:
            for i in range(100):
                yield str(i)
        finally:
            state["closed"] = True
    monkeypatch.setattr(llm, "_openai_stream", gen)
    stream = llm.chat_stream(MSGS)
    it = iter(stream)
    assert next(it) == "0"
    stream.close()
    assert state["closed"] is True


def test_chat_is_non_streaming(server):
    s = server((200, json.dumps({"choices": [{"message": {"content": "plain"}}], "usage": {"total_tokens": 2}}).encode()))
    r = llm.chat(MSGS)
    assert r.text == "plain" and s.requests[0]["body"]["stream"] is False


# ---------------------------------------------------------------------------------------- streamed tool calls

TOOLS = [{"type": "function", "function": {"name": "book", "description": "d",
                                           "parameters": {"type": "object", "properties": {"day": {"type": "string"}}}}}]


def tc(index=None, id=None, name=None, args=None, **extra):
    d = {}
    if index is not None:
        d["index"] = index
    if id is not None:
        d["id"] = id
    fn = {}
    if name is not None:
        fn["name"] = name
    if args is not None:
        fn["arguments"] = args
    if fn:
        d["function"] = fn
    return {"choices": [{"delta": {"tool_calls": [d], **extra}}]}


def test_openai_style_fragments_are_merged_by_index(server):
    s = server((200, sse(tc(0, "call_a", "book", ""), tc(0, None, None, '{"day": '), tc(0, None, None, '"mon"}'),
                         tc(1, "call_b", "cancel", '{"id": 7}'), "data: [DONE]")))
    stream = llm.chat_stream(MSGS, tools=TOOLS)
    assert list(stream) == []                                         # no text: the call is the reply
    calls = stream.result.tool_calls
    assert calls == [{"id": "call_a", "name": "book", "arguments": {"day": "mon"}},
                     {"id": "call_b", "name": "cancel", "arguments": {"id": 7}}]
    assert stream.tool_calls == calls
    assert s.requests[0]["body"]["tools"] == TOOLS and s.requests[0]["body"]["tool_choice"] == "auto"


def test_a_whole_call_in_one_chunk_without_index_or_id(server):
    server((200, sse(tc(None, None, "book", '{"day": "tue"}'), "data: [DONE]")))
    stream = llm.chat_stream(MSGS, tools=TOOLS)
    list(stream)
    [call] = stream.result.tool_calls
    assert call["name"] == "book" and call["arguments"] == {"day": "tue"} and call["id"].startswith("call_")


def test_several_calls_without_index_are_kept_apart_and_get_distinct_ids(server):
    server((200, sse(tc(None, "", "book", '{"day": "a"}'), tc(None, "", "book", '{"day": "b"}'), "data: [DONE]")))
    stream = llm.chat_stream(MSGS, tools=TOOLS)
    list(stream)
    calls = stream.result.tool_calls
    assert [c["arguments"] for c in calls] == [{"day": "a"}, {"day": "b"}]
    assert calls[0]["id"] != calls[1]["id"] and all(c["id"].startswith("call_") for c in calls)


def test_fragments_without_index_continue_the_call_before_them(server):
    server((200, sse(tc(None, "x1", "book", '{"day": '), tc(None, None, None, '"fri"}'), "data: [DONE]")))
    stream = llm.chat_stream(MSGS, tools=TOOLS)
    list(stream)
    assert stream.result.tool_calls == [{"id": "x1", "name": "book", "arguments": {"day": "fri"}}]


def test_text_and_a_tool_call_in_one_reply(server):
    server((200, sse(chunk("One moment."), tc(0, "c1", "book", "{}"), "data: [DONE]")))
    stream = llm.chat_stream(MSGS, tools=TOOLS)
    assert "".join(stream) == "One moment." and stream.result.tool_calls[0]["name"] == "book"
    assert stream.result.text == "One moment."
