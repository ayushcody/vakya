"""The primary hosted reply model (services/primary_llm.py, routed from services/llm.py): the OpenAI-shaped messages and tools become
a Messages API request, a streamed reply with a tool_use becomes the same tool_calls the turn pipeline already reads, the
parameters the model rejects are never sent, and the SDK's typed errors become LLMUnavailable. The SDK client is a stub:
nothing here touches the network."""
import json

import llm
import httpx
import pytest

from services import primary_llm, embeddings, llm
from llm_env import MSGS, use_endpoint


# ---- a stand-in for the SDK client: records what it was asked, answers with canned blocks ----

class Block:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class Usage:
    def __init__(self, i=11, o=7):
        self.input_tokens, self.output_tokens = i, o


class Msg:
    def __init__(self, content, stop="end_turn"):
        self.content, self.stop_reason, self.usage = content, stop, Usage()


def text(t):
    return Block("text", text=t)


def tool_use(id, name, **inp):
    return Block("tool_use", id=id, name=name, input=inp)


class FakeStream:
    def __init__(self, pieces, final, fail_after=None):
        self.text_stream = self._iter(pieces, fail_after)
        self._final = final
        self.closed = False

    def _iter(self, pieces, fail_after):
        for n, p in enumerate(pieces):
            if fail_after is not None and n == fail_after:
                raise llm.APIConnectionError(request=httpx.Request("POST", "https://llm.example/v1/messages"))
            yield p

    def get_final_message(self):
        return self._final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.closed = True


class FakeMessages:
    def __init__(self, reply=None, stream=None, error=None):
        self.reply, self.stream_obj, self.error, self.calls = reply, stream, error, []

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.reply

    def stream(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.stream_obj


class FakeClient:
    def __init__(self, messages):
        self.messages = messages


@pytest.fixture()
def primary(monkeypatch):
    """The primary model selected with a key; `install(messages)` puts a fake client in place of the SDK's."""
    use_endpoint(monkeypatch, key=None, model=None)
    monkeypatch.setenv("LLM_PRIMARY_KEY", "sk-test")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "model-small")
    primary_llm.reset_for_tests()
    holder = {}

    def install(messages):
        holder["m"] = messages
        monkeypatch.setattr(primary_llm, "client", lambda timeout=None: FakeClient(messages))
        return messages

    yield install
    primary_llm.reset_for_tests()


TOOLS = [{"type": "function", "function": {"name": "check_slot", "description": "Is a time free?",
                                           "parameters": {"type": "object", "properties": {"when": {"type": "string"}},
                                                          "required": ["when"]}}}]


# ---- conversion ----

def test_system_messages_are_joined_and_same_role_messages_merged():
    system, msgs = primary_llm.convert_messages([
        {"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"},
        {"role": "system", "content": "Answer in Hindi."}, {"role": "user", "content": "book me"},
        {"role": "assistant", "content": "Sure."}, {"role": "assistant", "content": "When?"}])
    assert system == "Be brief.\n\nAnswer in Hindi."
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert [b["text"] for b in msgs[0]["content"]] == ["hi", "book me"]
    assert [b["text"] for b in msgs[1]["content"]] == ["Sure.", "When?"]


def test_tools_become_name_description_input_schema():
    assert primary_llm.convert_tools(TOOLS) == [{"name": "check_slot", "description": "Is a time free?",
                                                "input_schema": TOOLS[0]["function"]["parameters"]}]


def test_a_tool_round_trip_becomes_tool_use_and_one_tool_result_message():
    calls = [{"id": "c1", "name": "check_slot", "arguments": {"when": "5 pm"}},
             {"id": "c2", "name": "check_slot", "arguments": {"when": "6 pm"}}]
    messages = [{"role": "user", "content": "free at 5 or 6?"}, llm.tool_call_message("Checking.", calls),
                {"role": "tool", "tool_call_id": "c1", "content": "free"},
                {"role": "tool", "tool_call_id": "c2", "content": "busy"}]
    _, msgs = primary_llm.convert_messages(messages)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[1]["content"][0] == {"type": "text", "text": "Checking."}
    uses = [b for b in msgs[1]["content"] if b["type"] == "tool_use"]
    assert [(u["id"], u["name"], u["input"]) for u in uses] == [("c1", "check_slot", {"when": "5 pm"}),
                                                                ("c2", "check_slot", {"when": "6 pm"})]
    assert msgs[2]["content"] == [{"type": "tool_result", "tool_use_id": "c1", "content": "free"},
                                  {"type": "tool_result", "tool_use_id": "c2", "content": "busy"}]


def test_a_user_note_after_tool_results_goes_in_the_same_message_after_them():
    messages = [{"role": "user", "content": "q"}, llm.tool_call_message(None, [{"id": "c1", "name": "t", "arguments": {}}]),
                {"role": "tool", "tool_call_id": "c1", "content": "ok"}, {"role": "user", "content": "now answer"}]
    _, msgs = primary_llm.convert_messages(messages)
    assert [b["type"] for b in msgs[-1]["content"]] == ["tool_result", "text"] and len(msgs) == 3


def test_a_call_with_no_result_gets_a_placeholder_and_arguments_that_are_not_json_become_empty():
    messages = [{"role": "user", "content": "q"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "x", "type": "function",
                                                                       "function": {"name": "t", "arguments": "not json"}}]}]
    _, msgs = primary_llm.convert_messages(messages)
    assert msgs[1]["content"] == [{"type": "tool_use", "id": "x", "name": "t", "input": {}}]
    assert msgs[2]["content"] == [{"type": "tool_result", "tool_use_id": "x", "content": "(no result)"}]


# ---- what is sent ----

def test_no_temperature_or_thinking_is_sent_and_effort_is_low_by_default(primary):
    fake = primary(FakeMessages(reply=Msg([text("Hello")])))
    before = llm.usage_today().get("primary", {"requests": 0, "tokens": 0})
    res = llm.chat([{"role": "system", "content": "s"}, *MSGS], temperature=0.7, max_tokens=160, tools=TOOLS)
    sent = fake.calls[0]
    for banned in ("temperature", "top_p", "top_k", "thinking"):
        assert banned not in sent
    assert sent["output_config"] == {"effort": "low"}
    assert sent["tool_choice"] == {"type": "auto"} and sent["model"] == "model-small"
    assert sent["system"][0]["text"] == "s" and sent["messages"][-1]["role"] == "user"   # no assistant prefill
    assert sent["tools"][0]["name"] == "check_slot"
    assert sent["max_tokens"] == 160 + 1024                                  # thinking counts toward max_tokens
    assert (res.text, res.provider, res.model) == ("Hello", "primary", "model-small")
    assert res.usage["total_tokens"] == 18 and llm.usage_today()["primary"] == {"requests": before["requests"] + 1, "tokens": before["tokens"] + 18}


def test_effort_and_model_come_from_the_settings(primary, monkeypatch):
    monkeypatch.setenv("LLM_EFFORT", "medium")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "model-large")
    monkeypatch.setenv("REASONING_TOKEN_ROOM", "100")
    fake = primary(FakeMessages(reply=Msg([text("x")])))
    llm.chat(MSGS, max_tokens=50)
    assert fake.calls[0]["output_config"] == {"effort": "medium"} and fake.calls[0]["model"] == "model-large"
    assert fake.calls[0]["max_tokens"] == 150 and "tools" not in fake.calls[0] and "tool_choice" not in fake.calls[0]
    monkeypatch.setenv("LLM_EFFORT", "bogus")
    assert primary_llm.effort() == "low"


def test_tool_use_in_a_blocking_reply_becomes_tool_calls_and_thinking_is_left_out(primary):
    reply = Msg([Block("thinking", thinking="...", signature="s"), text("One moment."), tool_use("tu1", "check_slot", when="5 pm")],
                stop="tool_use")
    primary(FakeMessages(reply=reply))
    res = llm.chat(MSGS, tools=TOOLS)
    assert res.text == "One moment."
    assert res.tool_calls == [{"id": "tu1", "name": "check_slot", "arguments": {"when": "5 pm"}}]
    back = llm.tool_call_message(res.text, res.tool_calls)           # what goes back into history: text and the call only
    assert back["tool_calls"][0]["function"] == {"name": "check_slot", "arguments": json.dumps({"when": "5 pm"})}


# ---- streaming ----

def test_streamed_text_and_a_tool_use_become_deltas_and_tool_calls(primary):
    final = Msg([Block("thinking", thinking="", signature="s"), text("Let me check."), tool_use("tu9", "check_slot", when="tomorrow 4 pm")],
                stop="tool_use")
    stream = FakeStream(["Let me ", "check."], final)
    fake = primary(FakeMessages(stream=stream))
    before = llm.usage_today().get("primary", {"requests": 0})["requests"]
    cs = llm.chat_stream(MSGS, tools=TOOLS, max_tokens=160)
    assert list(cs) == ["Let me ", "check."]
    assert cs.tool_calls == [{"id": "tu9", "name": "check_slot", "arguments": {"when": "tomorrow 4 pm"}}]
    assert cs.result.provider == "primary" and cs.result.text == "Let me check." and cs.result.usage["output_tokens"] == 7
    assert cs.result.tool_calls == cs.tool_calls and stream.closed
    assert "temperature" not in fake.calls[0] and fake.calls[0]["output_config"] == {"effort": "low"}
    assert llm.usage_today()["primary"]["requests"] == before + 1


def test_a_stream_that_is_only_a_tool_call_is_not_an_empty_reply(primary):
    primary(FakeMessages(stream=FakeStream([], Msg([tool_use("t", "check_slot", when="now")], stop="tool_use"))))
    cs = llm.chat_stream(MSGS, tools=TOOLS)
    assert list(cs) == [] and cs.tool_calls[0]["name"] == "check_slot"


def test_a_tool_call_cut_off_by_max_tokens_is_dropped(primary):
    primary(FakeMessages(stream=FakeStream(["Hmm"], Msg([text("Hmm"), tool_use("t", "check_slot")], stop="max_tokens"))))
    cs = llm.chat_stream(MSGS, tools=TOOLS)
    assert list(cs) == ["Hmm"] and cs.tool_calls == []


def test_a_failure_before_any_text_is_llm_unavailable_and_after_text_is_interrupted(primary):
    primary(FakeMessages(stream=FakeStream(["a"], Msg([text("a")]), fail_after=0)))
    with pytest.raises(llm.LLMUnavailable) as e:
        list(llm.chat_stream(MSGS))
    assert "primary" in e.value.reasons and "connection" in e.value.reasons["primary"]
    primary(FakeMessages(stream=FakeStream(["Hi ", "there"], Msg([text("Hi there")]), fail_after=1)))
    with pytest.raises(llm.LLMStreamInterrupted) as e2:
        list(llm.chat_stream(MSGS))
    assert e2.value.partial == "Hi " and e2.value.provider == "primary"


# ---- errors ----

def _status_error(cls, code):
    req = httpx.Request("POST", "https://llm.example/v1/messages")
    return cls("boom", response=httpx.Response(code, request=req), body=None)


def test_a_429_becomes_llm_unavailable_with_the_status_named(primary):
    primary(FakeMessages(error=_status_error(llm.RateLimitError, 429)))
    with pytest.raises(llm.LLMUnavailable) as e:
        llm.chat(MSGS)
    assert e.value.reasons["primary"] == "failed: language model rate limit reached (HTTP 429)"
    assert llm.last_failures["primary"].startswith("failed:")


def test_a_529_and_a_connection_error_become_llm_unavailable(primary):
    primary(FakeMessages(error=_status_error(llm.APIStatusError, 529)))
    with pytest.raises(llm.LLMUnavailable) as e:
        llm.chat(MSGS)
    assert "HTTP 529" in e.value.reasons["primary"]
    primary(FakeMessages(error=llm.APIConnectionError(request=httpx.Request("POST", "https://llm.example"))))
    with pytest.raises(llm.LLMUnavailable) as e2:
        llm.chat(MSGS)
    assert "connection error" in e2.value.reasons["primary"]


def test_a_failure_with_the_no_key_line_asked_for_still_raises(primary):
    primary(FakeMessages(error=_status_error(llm.RateLimitError, 429)))
    with pytest.raises(llm.LLMUnavailable):
        llm.chat(MSGS, no_key_line=True)


# ---- provider selection and status ----

def test_primary_model_is_the_default_when_an_provider_key_is_set_else_embeddings(monkeypatch):
    use_endpoint(monkeypatch, key=None, model=None)
    assert primary_llm.provider() == "embeddings" and llm.active_provider() == "openai-compatible"
    monkeypatch.setenv("LLM_PRIMARY_KEY", "sk-test")
    assert primary_llm.provider() == "primary" and llm.active_provider() == "primary"
    monkeypatch.setenv("LLM_PROVIDER", "embeddings")
    assert primary_llm.provider() == "embeddings"
    monkeypatch.setenv("LLM_PROVIDER", "primary")
    monkeypatch.delenv("LLM_PRIMARY_KEY")
    assert primary_llm.provider() == "primary" and llm.configured() is False
    assert llm.status()["reason"] == "Needs a language model key"


def test_the_embeddings_path_still_works_when_the_provider_is_embeddings(monkeypatch):
    use_endpoint(monkeypatch, key=None, model=None)
    monkeypatch.setenv("LLM_PRIMARY_KEY", "sk-test")
    monkeypatch.setenv("LLM_PROVIDER", "embeddings")
    monkeypatch.setattr(primary_llm, "client", lambda timeout=None: pytest.fail("the primary model must not be called"))
    assert llm.configured() is False                       # no Embeddings key yet
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "g")
    st = llm.status()
    assert st["configured"] is True and st["label"] == "Embeddings Flash" and st["model"] == embeddings.DEFAULT_CHAT_MODEL


def test_status_names_the_provider(primary, monkeypatch):
    st = llm.status()
    assert (st["provider"], st["label"], st["model"], st["configured"], st["reason"]) == \
        ("primary", "Hosted language model", "model-small", True, None)
    assert st["base_url"] == "" and st["keys"] == {"keys": 1, "available": 1}
    assert "sk-test" not in json.dumps(st)
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "model-large")
    assert llm.status()["label"] == "Hosted language model"
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "some-gateway-model")
    assert llm.status()["label"] == "Hosted language model"


def test_offline_mode_blocks_primary_model_like_any_hosted_model(primary, monkeypatch):
    monkeypatch.setenv("VAKYA_OFFLINE", "on")
    primary(FakeMessages(reply=Msg([text("no")])))
    assert llm.configured() is False and llm.status()["reason"].startswith("Offline mode")
    with pytest.raises(llm.LLMUnavailable):
        llm.chat(MSGS)
    assert llm.is_unconfigured(pytest.raises(llm.LLMUnavailable, llm.chat, MSGS).value)


def test_the_no_key_line_is_said_when_primary_model_has_no_key(monkeypatch):
    use_endpoint(monkeypatch, key=None, model=None)
    monkeypatch.setenv("LLM_PROVIDER", "primary")
    monkeypatch.setattr(primary_llm, "client", lambda timeout=None: pytest.fail("no request without a key"))
    res = llm.chat(MSGS, no_key_line=True, language="hi")
    assert res.provider == "no-key" and res.text == llm.no_key_reply("hi")
    with pytest.raises(llm.LLMUnavailable) as e:
        llm.chat(MSGS)
    assert llm.is_unconfigured(e.value)


def test_prewarm_targets_provider_when_primary_model_is_in_use(primary, monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY_BASE_URL", "https://llm.example")
    assert llm._prewarm_targets() == ["https://llm.example/"]
