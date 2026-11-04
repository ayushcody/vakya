"""In-call deadline: a phone turn caps the wait for the model's next bytes (PHONE_LLM_STALL_S), so a stalled model is given
up on before the caller hangs up and the turn says its fallback line instead of going silent."""
import pytest

from core.runtime import runtime as backend
from services import llm_limits


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("PHONE_LLM_STALL_S", raising=False)


def test_phone_stall_timeout_defaults_and_overrides(monkeypatch):
    assert backend.phone_stall_timeout() == 5.0
    monkeypatch.setenv("PHONE_LLM_STALL_S", "2.5")
    assert backend.phone_stall_timeout() == 2.5
    monkeypatch.setenv("PHONE_LLM_STALL_S", "nonsense")
    assert backend.phone_stall_timeout() == 5.0
    monkeypatch.setenv("PHONE_LLM_STALL_S", "0.1")
    assert backend.phone_stall_timeout() == 0.5             # never below half a second


def test_the_llm_timeout_is_the_callers_budget():
    assert llm_limits.llm_timeout_s(8.0) == 8.0 and backend.llm_timeout_s(20.0) == 20.0


def test_no_key_error_is_a_503_naming_the_key():
    from services import llm
    err = llm_limits.llm_unavailable(llm.LLMUnavailable({"openai-compatible": llm.NEEDS_KEY_MESSAGE}))
    assert err.status == 503 and "GEMINI_API_KEY" in err.message
