"""The engines Vakya speaks with: Gnani AI's Timbre voices (a preset voice) and Timbre voice cloning (a saved voice's speaker
embedding). Both are hosted: nothing loads on this machine, and with no GNANI_API_KEY a speaking endpoint answers with a clear
"Needs a Gnani key" error (see services/gnani.py and docs/GNANI.md)."""

try:
    from services import gnani
except ImportError:  # pragma: no cover
    from backend.services import gnani


# Gnani AI's hosted Timbre voices (a paid API: GNANI_API_KEY, see docs/GNANI.md): fast enough for live calls, Indian
# numbers and code-mixed Hinglish handled by the model. `timbre_clone` speaks a saved voice through Gnani's voice-clone
# embedding. The text (and, for cloning, the recording) travels to api.vachana.ai.
ENGINE_REGISTRY = {
    "timbre": {
        "name": "Timbre (Gnani)",
        "type": "preset",
        "languages": {"hi", "mr", "bn", "ta", "te", "kn", "ml", "gu", "pa", "en", "hi-en"},
    },
    "timbre_clone": {
        "name": "Timbre voice cloning (Gnani)",
        "type": "clone",
        "languages": {"hi", "mr", "bn", "ta", "te", "kn", "ml", "gu", "pa", "en", "hi-en"},
    },
}

# The engine a saved voice speaks with
CLONE_ENGINE = "timbre_clone"
CLONE_ENGINES = (CLONE_ENGINE,)
MODEL_NAME = ENGINE_REGISTRY["timbre"]["name"]
INDIC_LANGUAGES = {"hi", "mr", "bn", "gu", "kn", "ml", "or", "pa", "ta", "te", "as"}


def hosted_engine_flag(key: str) -> dict:
    """{"available": bool, "unavailable_reason": ...} for Gnani's hosted engines, so the app can show "Timbre (Gnani)"
    greyed out until GNANI_API_KEY is set; {} for an unknown engine."""
    if key not in gnani.API_ENGINES:
        return {}
    reason = gnani.engine_unavailable_reason()
    return {"available": reason is None, "unavailable_reason": reason}


def route_engine(language: str, mode: str = "fast", clone_required: bool = False) -> str:
    """The engine that speaks: Timbre voice cloning for a saved voice, Timbre otherwise. The key is checked when the
    engine speaks (gnani.synth raises "Needs a Gnani key"), so routing never fails."""
    return gnani.TIMBRE_CLONE_ENGINE if clone_required else gnani.TIMBRE_ENGINE


ENGINE_ROUTE_REASON = {
    "timbre": "Timbre preset voice (Gnani AI API)",
    "timbre_clone": "Cloned voice: Timbre voice cloning (Gnani AI API)",
}
