"""Which model services/llm.py talks to (base URL, model, key), what status() says, and the fixed line said when there is
no key: nothing is sent, nothing raises on the spoken path."""
import pytest

from services import embeddings, llm
from llm_env import MSGS, use_endpoint


@pytest.fixture()
def clean(monkeypatch):
    use_endpoint(monkeypatch, key=None, model=None)
    monkeypatch.setattr(llm, "_client", lambda: pytest.fail("no request may be made without a key"))


def test_defaults_are_embeddingss_openai_compatible_endpoint_and_stable_flash(clean):
    assert embeddings.base_url() == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert embeddings.chat_model() == "embeddings-3.6-flash"
    assert llm.status()["base_url"] == embeddings.base_url() and llm.status()["model"] == embeddings.chat_model()


def test_settings_come_from_the_llm_variables(clean, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "some-model")
    st = llm.status()
    assert st["base_url"] == "https://example.test/v1" and st["model"] == "some-model"


def test_the_key_is_llm_api_key_else_embeddings_api_key(clean, monkeypatch):
    assert embeddings.api_key() == "" and llm.configured() is False
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "g-key")
    assert embeddings.api_key() == "g-key" and llm.configured() is True
    monkeypatch.setenv("LLM_API_KEY", "l-key")
    assert embeddings.api_key() == "l-key"
    monkeypatch.delenv("EMBEDDINGS_API_KEY")
    assert embeddings.api_key() == "l-key"


def test_status_without_and_with_a_key(clean, monkeypatch):
    st = llm.status()
    assert st == {"provider": "openai-compatible", "base_url": embeddings.DEFAULT_BASE_URL, "model": embeddings.DEFAULT_CHAT_MODEL,
                  "configured": False, "reason": "Needs a Embeddings key", "keys": {"keys": 0, "available": 0}}
    assert llm.NEEDS_KEY_MESSAGE == "Needs a Embeddings key"
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "k")
    st = llm.status()
    assert st["configured"] is True and st["reason"] is None


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
    assert e.value.reasons == {"openai-compatible": "Needs a Embeddings key"}


def test_stream_without_a_key_is_one_chunk(clean):
    stream = llm.chat_stream(MSGS, no_key_line=True, language="ta")
    assert list(stream) == [llm.no_key_reply("ta")]
    assert stream.result.text == llm.no_key_reply("ta") and stream.result.provider == llm.NO_KEY_PROVIDER
    with pytest.raises(llm.LLMUnavailable):
        list(llm.chat_stream(MSGS))


def test_offline_mode_blocks_a_hosted_endpoint(clean, monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "k")
    monkeypatch.setenv("VAKYA_OFFLINE", "1")
    st = llm.status()
    assert st["configured"] is False and "Offline mode" in st["reason"]
    assert llm.chat(MSGS, no_key_line=True).text == llm.no_key_reply("en")


def test_prewarm_targets_only_a_configured_model(clean, monkeypatch):
    assert llm._prewarm_targets() == []
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "k")
    assert llm._prewarm_targets() == ["https://generativelanguage.googleapis.com/"]
