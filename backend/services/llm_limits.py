"""How long a turn waits for the language model, and the error the endpoints raise when no model can answer."""

import os

try:
    from core.errors import AppError
    from services import llm
except ImportError:  # pragma: no cover
    from backend.core.errors import AppError
    from backend.services import llm


LLM_UNAVAILABLE_MSG = "No language model is available. Add GEMINI_API_KEY to backend/.env."


def llm_unavailable(e: "llm.LLMUnavailable") -> AppError:
    return AppError("LLM_UNAVAILABLE", LLM_UNAVAILABLE_MSG, 503, {"reasons": e.reasons})


def llm_timeout_s(seconds: float) -> float:
    """How long a request may wait for the hosted model (the callers' budget, unchanged)."""
    return seconds


def _env_seconds(name: str, default: float) -> float:
    try:
        return max(0.5, float(os.getenv(name, str(default)) or default))
    except ValueError:
        return default


def phone_stall_timeout() -> float:
    """The longest a phone turn waits for the model's next bytes before it gives up: PHONE_LLM_STALL_S (default 5 s).
    A caller hears a long silence as a dropped call; the filler (PHONE_FILLER_AFTER_MS) covers the first part of it."""
    return _env_seconds("PHONE_LLM_STALL_S", 5.0)
