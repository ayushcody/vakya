"""Tool calling through the OpenAI-compatible endpoint (blocking): tools go out in the OpenAI format with tool_choice
"auto" only, one or several tool_calls come back, a call with no id gets a made-up one that is used on both sides, the
assistant message is re-sent as it was, and the system message stays first."""
import json

import pytest

from services import llm
from llm_env import FakeServer, MSGS, reply, use_endpoint

TOOLS = [{"type": "function", "function": {"name": "book", "description": "Book a slot",
                                           "parameters": {"type": "object", "properties": {"day": {"type": "string"}}}}}]


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


def call(id, name, args, **extra):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}, **extra}


def test_a_tool_round_trip(server):
    s = server((200, reply({"content": None, "tool_calls": [{"type": "function", "function": {
                    "name": "book", "arguments": '{"day": "mon"}'}}]})),       # no id, as Gemini may send it
               (200, reply({"content": "Booked for Monday."})))
    msgs = [{"role": "system", "content": "You are a receptionist."}, {"role": "user", "content": "book monday"}]
    first = llm.chat(msgs, tools=TOOLS, tool_choice="auto", max_tokens=100)
    [tc] = first.tool_calls
    assert tc["name"] == "book" and tc["arguments"] == {"day": "mon"} and tc["id"].startswith("call_")
    body = s.requests[0]["body"]
    assert body["tools"] == TOOLS and body["tool_choice"] == "auto" and body["messages"][0]["role"] == "system"
    assert "response_format" not in body and "frequency_penalty" not in body

    followup = [*msgs, llm.tool_call_message(first.text, first.tool_calls),
                {"role": "tool", "tool_call_id": tc["id"], "content": "ok: slot 3"}]
    second = llm.chat(followup, tools=TOOLS, max_tokens=100)
    assert second.text == "Booked for Monday." and second.tool_calls == []
    sent = s.requests[1]["body"]["messages"]
    assert sent[2]["role"] == "assistant" and sent[2]["tool_calls"][0]["id"] == tc["id"]
    assert sent[2]["tool_calls"][0]["function"] == {"name": "book", "arguments": '{"day": "mon"}'}
    assert sent[3] == {"role": "tool", "tool_call_id": tc["id"], "content": "ok: slot 3"}


def test_several_tool_calls_in_one_reply(server):
    server((200, reply({"content": None, "tool_calls": [call("a", "book", {"day": "mon"}),
                                                        call("", "book", {"day": "tue"}),
                                                        call(None, "book", {"day": "wed"})]})))
    res = llm.chat(MSGS, tools=TOOLS)
    assert [c["arguments"]["day"] for c in res.tool_calls] == ["mon", "tue", "wed"]
    ids = [c["id"] for c in res.tool_calls]
    assert ids[0] == "a" and len(set(ids)) == 3 and all(i.startswith("call_") for i in ids[1:])
    message = llm.tool_call_message(res.text, res.tool_calls)
    assert [c["id"] for c in message["tool_calls"]] == ids and message["content"] is None


def test_arguments_that_are_not_json_become_an_empty_dict(server):
    server((200, reply({"content": "", "tool_calls": [{"id": "z", "type": "function",
                                                       "function": {"name": "book", "arguments": "not json"}}]})))
    assert llm.chat(MSGS, tools=TOOLS).tool_calls == [{"id": "z", "name": "book", "arguments": {}}]


def test_extra_content_on_a_call_goes_back_with_it(server):
    extra = {"google": {"thought_signature": "sig"}}
    server((200, reply({"content": None, "tool_calls": [call("a", "book", {}, extra_content=extra)]})))
    res = llm.chat(MSGS, tools=TOOLS)
    assert res.tool_calls[0]["extra_content"] == extra
    assert llm.tool_call_message(None, res.tool_calls)["tool_calls"][0]["extra_content"] == extra


def test_the_final_round_is_asked_without_tools_and_in_words(server):
    s = server((200, reply({"content": "All done."})))
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "book"},
            llm.tool_call_message(None, [{"id": "c1", "name": "book", "arguments": {"day": "mon"}}]),
            {"role": "tool", "tool_call_id": "c1", "content": "ok"}]
    res = llm.chat(msgs, tools=TOOLS, tool_choice="none")
    body = s.requests[0]["body"]
    assert res.text == "All done." and "tools" not in body and "tool_choice" not in body
    assert all(m["role"] in ("system", "user", "assistant") for m in body["messages"])
    assert "[called: book(" in body["messages"][2]["content"] and "plain spoken text" in body["messages"][-1]["content"]


def test_tool_choice_is_never_anything_but_auto(server):
    s = server((200, reply()))
    llm.chat(MSGS, tools=TOOLS, tool_choice="required")
    assert s.requests[0]["body"]["tool_choice"] == "auto"


def test_a_later_system_message_is_joined_onto_the_first(server):
    s = server((200, reply({"content": '{"a": 1}'})))
    msgs = [{"role": "system", "content": "first"}, {"role": "user", "content": "q"},
            {"role": "system", "content": "Respond with a single JSON object only."}]
    llm.chat(msgs)
    sent = s.requests[0]["body"]["messages"]
    assert sent[0] == {"role": "system", "content": "first\n\nRespond with a single JSON object only."}
    assert [m["role"] for m in sent] == ["system", "user"]


def test_json_mode_asks_in_the_prompt_and_reads_the_object(server):
    s = server((200, reply({"content": 'Sure: ```json\n{"ok": true}\n```'})))
    res = llm.chat([{"role": "user", "content": "give facts"}], json_mode=True)
    assert json.loads(res.text) == {"ok": True}
    body = s.requests[0]["body"]
    assert "response_format" not in body and body["messages"][0]["role"] == "system"
    assert "JSON" in body["messages"][0]["content"]


def test_a_tool_turn_through_the_tool_loop(server, monkeypatch):
    from services import tools
    monkeypatch.setenv("VAKYA_TOOL_PROTOCOL", "native")
    s = server((200, reply({"content": None, "tool_calls": [{"type": "function", "function": {
                    "name": "echo", "arguments": '{"text": "hello"}'}}]})),
               (200, reply({"content": "It says hello."})))
    spec = tools.ToolSpec(server="test", tool="echo", description="Echo", schema={
        "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})
    monkeypatch.setattr(tools, "execute", lambda spec, args, **kw: tools.ToolCall(spec.name, args, True, "echoed", 1, "echoed"))
    turn = tools.run_tool_turn([{"role": "system", "content": "s"}, {"role": "user", "content": "echo hello"}], [spec])
    assert turn.text == "It says hello." and turn.protocol == "native"
    second = s.requests[1]["body"]["messages"]
    ids = {m["tool_call_id"] for m in second if m["role"] == "tool"}
    assert ids == {c["id"] for m in second if m["role"] == "assistant" for c in m.get("tool_calls", [])} and len(ids) == 1
