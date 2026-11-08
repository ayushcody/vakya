"""Which model services/llm.py talks to (base URL, model, key), what status() says, and the fixed line said when there is
no key: nothing is sent, nothing raises on the spoken path."""
import pytest

from services import llm, model_config
from llm_env import MSGS, use_endpoint


def set_all(monkeypatch, url="https://llm.example.test/v1"):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", url)
    monkeypatch.setenv("LLM_MODEL", "m")


@pytest.fixture()
def clean(monkeypatch):
    use_endpoint(monkeypatch, key=None, model=None)
    monkeypatch.setattr(llm, "_client", lambda: pytest.fail("no request may be made without a key"))


def test_nothing_is_built_in_so_unset_settings_are_empty_and_named(clean):
    assert model_config.base_url() == "" and model_config.chat_model() == "" and model_config.api_key() == ""
    reason = model_config.llm_missing()
    assert all(n in reason for n in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"))
    assert llm.status()["base_url"] == "" and llm.status()["model"] == ""


def test_settings_come_from_the_llm_variables(clean, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "some-model")
    st = llm.status()
    assert st["base_url"] == "https://example.test/v1" and st["model"] == "some-model"


def test_all_three_settings_are_needed(clean, monkeypatch):
    assert model_config.api_key() == "" and llm.configured() is False
    monkeypatch.setenv("LLM_API_KEY", "l-key")
    assert model_config.api_key() == "l-key" and llm.configured() is False
    assert "LLM_BASE_URL" in llm.status()["reason"] and "LLM_MODEL" in llm.status()["reason"] and "LLM_API_KEY" not in llm.status()["reason"]
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "m")
    assert llm.configured() is True


def test_the_older_setting_names_are_still_read(clean, monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY_KEY", "old-key")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "old-model")
    monkeypatch.setenv("LLM_PRIMARY_BASE_URL", "https://old.example.test/v1")
    assert (model_config.api_key(), model_config.chat_model(), model_config.base_url()) == ("old-key", "old-model", "https://old.example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "new-key")
    assert model_config.api_key() == "new-key"


def test_status_without_and_with_the_settings(clean, monkeypatch):
    st = llm.status()
    assert st["provider"] == "openai-compatible" and st["label"] == "Hosted language model"
    assert st["configured"] is False and st["reason"].startswith(llm.NEEDS_KEY_MESSAGE) and st["keys"] == {"keys": 0, "available": 0}
    assert llm.NEEDS_KEY_MESSAGE == "Needs a language model"
    set_all(monkeypatch)
    st = llm.status()
    assert st["configured"] is True and st["reason"] is None and st["keys"] == {"keys": 1, "available": 1}


LANGS = ["hi", "en", "mr", "bn", "ta", "te", "kn", "ml", "gu", "pa", "hinglish"]


def test_the_no_key_line_is_in_the_callers_language(clean):
    lines = {lang: llm.no_key_reply(lang) for lang in LANGS}
    assert lines["en"] == "Sorry, the assistant is not connected yet. Please try again shortly."
    assert len(set(lines.values())) == len(LANGS) and all(lines.values())
    assert any("ऀ" <= c <= "ॿ" for c in lines["hi"])      # Devanagari
    assert any("஀" <= c <= "௿" for c in lines["ta"])      # Tamil


def test_an_unknown_or_missing_language_falls_back_to_english(clean):
    english = llm.no_key_reply("en")
    for lang in (None, "", "xx", "fr"):
        assert llm.no_key_reply(lang) == english
    assert llm.no_key_reply("HI-IN") == llm.no_key_reply("hi")


def test_chat_without_a_key_returns_the_line_instead_of_raising(clean):
    res = llm.chat(MSGS, no_key_line=True, language="mr")
    assert res.text == llm.no_key_reply("mr") and res.provider == llm.NO_KEY_PROVIDER and res.tool_calls == []


def test_a_side_task_without_a_key_gets_llm_unavailable(clean):
    with pytest.raises(llm.LLMUnavailable) as e:
        llm.chat(MSGS, json_mode=True)
    assert list(e.value.reasons) == ["openai-compatible"] and e.value.reasons["openai-compatible"].startswith(llm.NEEDS_KEY_MESSAGE)


def test_stream_without_a_key_is_one_chunk(clean):
    stream = llm.chat_stream(MSGS, no_key_line=True, language="ta")
    assert list(stream) == [llm.no_key_reply("ta")]
    assert stream.result.text == llm.no_key_reply("ta") and stream.result.provider == llm.NO_KEY_PROVIDER
    with pytest.raises(llm.LLMUnavailable):
        list(llm.chat_stream(MSGS))


def test_offline_mode_blocks_a_hosted_endpoint(clean, monkeypatch):
    set_all(monkeypatch)
    monkeypatch.setenv("VAKYA_OFFLINE", "1")
    st = llm.status()
    assert st["configured"] is False and "Offline mode" in st["reason"]
    assert llm.chat(MSGS, no_key_line=True).text == llm.no_key_reply("en")


def test_prewarm_targets_only_a_configured_model(clean, monkeypatch):
    assert llm._prewarm_targets() == []
    set_all(monkeypatch)
    assert llm._prewarm_targets() == ["https://llm.example.test/"]
