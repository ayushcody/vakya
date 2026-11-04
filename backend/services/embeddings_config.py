"""Where Google's Embeddings is reached from: the language model (Embeddings Flash through the OpenAI-compatible endpoint) and the
embeddings that knowledge search uses. One place for the base URL, the model ids and the key, so llm.py and retrieval.py
agree. Nothing here makes a request.

Sources (read 4 Nov 2026):
  * OpenAI compatibility, base URL, tools and streaming: https://ai.google.dev/embeddings-api/docs/openai
      base URL      https://generativelanguage.googleapis.com/v1beta/openai/
      chat example  model "embeddings-3.8-flash", tools in the OpenAI format ({"type": "function", "function": {...}}),
                    tool_choice "auto", "role": "system" as the first message, stream=True (stream_options include_usage).
      The page documents no tool_choice other than "auto" and says nothing about parallel tool calls: send "auto", and
      handle one or several tool_calls in a reply (the calling code already loops over them).
  * Models: https://ai.google.dev/embeddings-api/docs/models  ("embeddings-3.6-flash" is the stable Flash, "embeddings-3.8-flash" is
      marked new, "embeddings-3-flash-preview" is a preview; "embeddings-embedding-2" and "embeddings-embedding-001" embed text).
  * Embeddings: https://ai.google.dev/embeddings-api/docs/embeddings
      REST  POST https://generativelanguage.googleapis.com/v1beta/models/embeddings-embedding-2:batchEmbedContents
            header x-goog-api-key; body {"requests": [{"model": "models/embeddings-embedding-2", "content": {"parts":
            [{"text": ...}]}, "outputDimensionality": N}, ...]}
      default 3072 dimensions, any 128..3072 (768, 1536, 3072 recommended); embeddings-embedding-2 normalises a truncated
      vector itself, embeddings-embedding-001 does not (so we always normalise); inputs over 8,192 tokens are truncated.
      embeddings-embedding-2 has no task_type: the text is prefixed instead ("task: search result | query: {text}" for a query,
      "title: {title} | text: {text}" for a passage).
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_CHAT_MODEL = "embeddings-3.6-flash"
DEFAULT_EMBED_MODEL = "embeddings-embedding-2"
DEFAULT_EMBED_DIM = 768
EMBED_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"
GOOGLE_HOST = "generativelanguage.googleapis.com"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def base_url() -> str:
    """LLM_BASE_URL, else Embeddings's OpenAI-compatible endpoint."""
    return _env("LLM_BASE_URL", DEFAULT_BASE_URL)


def chat_model() -> str:
    """LLM_MODEL, else the stable Embeddings Flash."""
    return _env("LLM_MODEL", DEFAULT_CHAT_MODEL)


def api_key() -> str:
    """The language-model key: LLM_API_KEY, else EMBEDDINGS_API_KEY. Empty when neither is set."""
    return _env("LLM_API_KEY") or _env("EMBEDDINGS_API_KEY")


def uses_embeddings() -> bool:
    return GOOGLE_HOST in base_url()


def embed_key() -> str:
    """The embeddings key: EMBEDDINGS_API_KEY, else LLM_API_KEY when the language model is Embeddings too. Empty when none."""
    return _env("EMBEDDINGS_API_KEY") or (_env("LLM_API_KEY") if uses_embeddings() else "")


def embed_model() -> str:
    """EMBEDDINGS_EMBED_MODEL, else embeddings-embedding-2."""
    return _env("EMBEDDINGS_EMBED_MODEL", DEFAULT_EMBED_MODEL)


def embed_dim() -> int:
    """EMBEDDINGS_EMBED_DIM (128..3072), else 768."""
    try:
        n = int(_env("EMBEDDINGS_EMBED_DIM", str(DEFAULT_EMBED_DIM)))
    except ValueError:
        n = DEFAULT_EMBED_DIM
    return max(128, min(3072, n))


def llm_configured() -> bool:
    return bool(api_key())


def embeddings_configured() -> bool:
    return bool(embed_key())


def missing_key_reason() -> Optional[str]:
    return None if llm_configured() else "Needs a Embeddings key (set EMBEDDINGS_API_KEY)."
