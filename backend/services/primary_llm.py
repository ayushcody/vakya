"""The primary hosted reply model: the adapter between services/llm.py and the provider's official Python SDK.

llm.py builds every request in the OpenAI chat shape (messages with `role: tool` results and assistant `tool_calls`, tools as
{"type": "function", "function": {...}}). This module turns that into the provider's Messages request, makes it with the SDK
(a blocking call or a streamed one) and turns the answer back into what llm.py returns: the text, the usage, and the tool calls
as [{"id", "name", "arguments": dict}]. It makes no request on import and holds no state beyond one cached client.

Settings (backend/.env):
  LLM_PROVIDER          primary or embeddings. Unset: primary when LLM_PRIMARY_KEY is set, else embeddings (services/embeddings.py)
  LLM_PRIMARY_KEY       the key (secret)
  LLM_PRIMARY_MODEL     the model id (required with the key; kept in backend/.env only)
  LLM_PRIMARY_BASE_URL  optional; read here so offline mode can tell a local gateway from the hosted API
  LLM_EFFORT            low | medium | high | xhigh | max, default low: phone replies must be fast, and effort is the only dial
                        for how long the model thinks before it speaks
  LLM_PROMPT_CACHE      on (default) or off: put cache_control breakpoints on the stable start of every request (see below)
  LLM_RETRIES           how often the SDK retries a 429, 5xx or dropped connection (default 1 here: a spoken turn cannot wait)

What the model takes (read 8 Nov 2026 in the provider's model notes and SDK):
  * Thinking cannot be switched off, so `thinking` is left out and `output_config={"effort": ...}` sets its depth. Thinking
    tokens count toward max_tokens, so the caller's budget gets REASONING_TOKEN_ROOM added (llm._with_reasoning_room).
  * temperature, top_p and top_k are rejected, so none is sent.
  * A forced tool_choice is rejected, so a request with tools sends {"type": "auto"} and nothing else.
  * No assistant prefill: the last message is never an assistant turn this module makes up.
  * Thinking blocks are bound to the conversation that produced them; only `text` and `tool_use` blocks are kept.
  * Tool input arrives as a JSON object; a reply cut off by max_tokens may carry a half-written tool call, which is dropped.
Prompt caching: one ephemeral `cache_control` breakpoint on the system block (tools and system prompt are static per agent) and
one on the last block before the caller's newest message. A prefix under the provider's minimum is silently not cached;
`usage_dict` reports cache reads and writes so a zero read across a call shows a silent invalidator.
Errors are the SDK's typed ones; `error_text` names them in one plain sentence, and llm.py raises LLMUnavailable (nothing said
yet) or LLMStreamInterrupted (some text said).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Dict, Iterator, List, Optional, Tuple

try:
    import llm
except ImportError:  # pragma: no cover - the package is in requirements.txt; without it this provider is unavailable
    llm = None

log = logging.getLogger("vakya.primary_llm")

PROVIDER = "primary"
DEFAULT_MODEL = ""   # the model id lives in backend/.env (LLM_PRIMARY_MODEL)
DEFAULT_BASE_URL = ""  # empty: the SDK's own default endpoint
DEFAULT_EFFORT = "low"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
NEEDS_KEY_MESSAGE = "Needs a language model key"


# ---------------------------------------------------------------------------------------------- settings

def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def api_key() -> str:
    """LLM_PRIMARY_KEY (quotes stripped), empty when unset."""
    return _env("LLM_PRIMARY_KEY").strip("\"'")


def base_url() -> str:
    """LLM_PRIMARY_BASE_URL, empty for the SDK's default endpoint."""
    return _env("LLM_PRIMARY_BASE_URL", DEFAULT_BASE_URL)


def model() -> str:
    """LLM_PRIMARY_MODEL (empty when unset)."""
    return _env("LLM_PRIMARY_MODEL", DEFAULT_MODEL)


def effort() -> str:
    """LLM_EFFORT (low, medium, high, xhigh or max), default low; any other value is low."""
    raw = _env("LLM_EFFORT", DEFAULT_EFFORT).lower()
    return raw if raw in EFFORTS else DEFAULT_EFFORT


def provider() -> str:
    """The reply model in use: "primary" or "embeddings". LLM_PROVIDER decides; unset (or not one of the two), the primary model is used
    when LLM_PRIMARY_KEY is set and Embeddings otherwise."""
    raw = _env("LLM_PROVIDER").lower()
    if raw == "primary":
        return "primary"
    if raw in ("embeddings", "google", "openai", "openai-compatible"):
        return "embeddings"
    return "primary" if api_key() else "embeddings"


def label(model_id: Optional[str] = None) -> str:
    """A name for people. The model is not named in the product."""
    return "Hosted language model"


def sdk_missing() -> bool:
    return llm is None


# ---------------------------------------------------------------------------------------------- messages and tools

def _text_of(content: Any) -> str:
    """The text of an OpenAI message `content`: a string, or a list of parts (only the text parts count)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text") or "" for p in content if isinstance(p, dict) and p.get("type") in (None, "text"))
    return str(content)


def _args(raw: Any) -> dict:
    """The arguments of an OpenAI tool call (a JSON string, or already a dict) as a dict; {} when they are not JSON."""
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _blocks(content: Any) -> List[dict]:
    return [dict(b) for b in content] if isinstance(content, list) else ([{"type": "text", "text": content}] if content else [])


def convert_messages(messages: List[dict]) -> Tuple[str, List[dict]]:
    """OpenAI-format `messages` as (system, Messages API messages).

    System messages are joined into the system string. An assistant message with `tool_calls` becomes text plus `tool_use`
    blocks; `role: tool` messages become `tool_result` blocks, all of one batch in a single user message (put first, as the API
    wants). Consecutive messages of one role are merged, empty text is dropped, a tool call with no result gets a placeholder
    result and a result with no call is written as text, so the request is always one the API accepts."""
    system: List[str] = []
    out: List[dict] = []

    def add(role: str, blocks: List[dict]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        role = m.get("role")
        if role == "system":
            text = _text_of(m.get("content")).strip()
            if text:
                system.append(text)
        elif role == "assistant":
            blocks: List[dict] = []
            text = _text_of(m.get("content"))
            if text.strip():
                blocks.append({"type": "text", "text": text})
            for i, tc in enumerate(m.get("tool_calls") or []):
                fn = tc.get("function") or {}
                blocks.append({"type": "tool_use", "id": tc.get("id") or f"call_{len(out)}_{i}",
                               "name": fn.get("name") or "", "input": _args(fn.get("arguments"))})
            add("assistant", blocks)
        elif role == "tool":
            add("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id") or "", "content": _text_of(m.get("content"))}])
        else:  # user (or anything unknown, said as the user)
            text = _text_of(m.get("content"))
            add("user", [{"type": "text", "text": text}] if text.strip() else [])
    _pair_tool_calls(out)
    return "\n\n".join(system), out


def _pair_tool_calls(msgs: List[dict]) -> None:
    """In place: every tool_result follows the assistant message that holds its tool_use (first in its user message), a tool_use
    with no result gets a placeholder one, and a tool_result with no tool_use is turned into text."""
    i = 0
    while i < len(msgs):
        m = msgs[i]
        prev = msgs[i - 1] if i > 0 else None
        if m["role"] == "assistant":
            wanted = [b["id"] for b in m["content"] if b.get("type") == "tool_use"]
            nxt = msgs[i + 1] if i + 1 < len(msgs) and msgs[i + 1]["role"] == "user" else None
            have = {b.get("tool_use_id") for b in (nxt["content"] if nxt else []) if b.get("type") == "tool_result"}
            missing = [{"type": "tool_result", "tool_use_id": t, "content": "(no result)"} for t in wanted if t not in have]
            if missing and nxt is None:
                msgs.insert(i + 1, {"role": "user", "content": missing})
            elif missing:
                nxt["content"][0:0] = missing
        else:
            known = {b["id"] for b in (prev["content"] if prev and prev["role"] == "assistant" else []) if b.get("type") == "tool_use"}
            m["content"] = [b if b.get("type") != "tool_result" or b.get("tool_use_id") in known
                            else {"type": "text", "text": f"Tool result: {b.get('content') or ''}"} for b in m["content"]]
            m["content"].sort(key=lambda b: 0 if b.get("type") == "tool_result" else 1)  # tool results first
        i += 1


def convert_tools(tools: Optional[List[dict]]) -> List[dict]:
    """OpenAI function tools as Messages API tools: {"name", "description", "input_schema"}."""
    out = []
    for t in tools or []:
        fn = t.get("function") if t.get("type") == "function" else t
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        schema = fn.get("parameters") or fn.get("input_schema") or {"type": "object", "properties": {}}
        out.append({"name": fn["name"], "description": fn.get("description") or "", "input_schema": schema})
    return out


CACHE_BREAKPOINT = {"type": "ephemeral"}


def cache_enabled() -> bool:
    """LLM_PROMPT_CACHE (default on): cache_control breakpoints on the stable start of every request."""
    return _env("LLM_PROMPT_CACHE", "on").lower() not in ("off", "0", "false", "no")


def build_params(messages: List[dict], *, max_tokens: int, tools: Optional[List[dict]] = None) -> dict:
    """The keyword arguments of `client.messages.create` / `.stream`. `max_tokens` already includes the room thinking needs.
    No temperature, top_p, top_k, thinking or prefill is sent (see the module note); tools go with tool_choice auto."""
    system, msgs = convert_messages(messages)
    params: Dict[str, Any] = {"model": model(), "max_tokens": int(max_tokens), "messages": msgs,
                              "output_config": {"effort": effort()}}
    cache = cache_enabled()
    if system:
        params["system"] = [{"type": "text", "text": system, **({"cache_control": CACHE_BREAKPOINT} if cache else {})}]
    if cache and len(msgs) >= 2 and msgs[-2]["content"]:
        msgs[-2]["content"][-1]["cache_control"] = CACHE_BREAKPOINT   # the call so far; only its end grows
    converted = convert_tools(tools)
    if converted:
        params["tools"] = converted
        params["tool_choice"] = {"type": "auto"}
    return params


# ---------------------------------------------------------------------------------------------- the client

_client_lock = threading.Lock()
_client_cache: Dict[tuple, Any] = {}


def _retries() -> int:
    try:
        return max(0, int(os.getenv("LLM_RETRIES", "") or 1))
    except ValueError:
        return 1


def client(timeout: Optional[float] = None):
    """The shared SDK client (one connection pool per key, URL and retry count), with `timeout` seconds per wait."""
    if llm is None:
        raise RuntimeError("the language model SDK is not installed")
    key = (api_key(), base_url(), _retries())
    with _client_lock:
        c = _client_cache.get(key)
        if c is None:
            c = _client_cache[key] = llm.LLM provider(api_key=key[0], base_url=key[1] or None, max_retries=key[2])
    return c.with_options(timeout=timeout) if timeout else c


def reset_for_tests() -> None:
    with _client_lock:
        _client_cache.clear()


def endpoint() -> str:
    """Where requests go: LLM_PRIMARY_BASE_URL, else the SDK client's own default; empty when neither is known."""
    if base_url():
        return base_url()
    try:
        return str(getattr(client(), "base_url", "") or "")
    except Exception:  # noqa: BLE001 - no SDK or no key: nothing to warm
        return ""


def prewarm_connection(url: str) -> None:
    """Open a TCP + TLS connection to `url` on the SDK client's own pool, so the first reply reuses it. No key is sent."""
    http = getattr(client(), "_client", None)  # the SDK's httpx client; not part of its public API, so any failure is ignored
    if http is not None:
        http.head(url, headers={"Accept-Encoding": "identity"}, timeout=5.0)


# ---------------------------------------------------------------------------------------------- answers

def usage_dict(usage: Any) -> Dict[str, int]:
    """The SDK usage object as the dict llm.py keeps: input_tokens, output_tokens and total_tokens (the OpenAI names too)."""
    i = int(getattr(usage, "input_tokens", 0) or 0)
    o = int(getattr(usage, "output_tokens", 0) or 0)
    out = {"input_tokens": i, "output_tokens": o, "prompt_tokens": i, "completion_tokens": o, "total_tokens": i + o}
    for extra in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        v = getattr(usage, extra, None)
        if v:
            out[extra] = int(v)
    return out


def _tool_input(raw: Any) -> dict:
    return _args(raw)  # a dict as the SDK parsed it, or a JSON string (json.loads); never searched as text


def parse_message(msg: Any) -> Tuple[str, List[dict], Dict[str, int], Optional[str]]:
    """(text, tool_calls, usage, stop_reason) of a finished Message. Only `text` and `tool_use` blocks are read: thinking
    blocks are left behind, so they never reach Vakya's history. When max_tokens cut the reply off, a last tool call is dropped
    (its input may be half written)."""
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    calls = [{"id": b.id, "name": b.name, "arguments": _tool_input(b.input)}
             for b in msg.content if getattr(b, "type", None) == "tool_use"]
    stop = getattr(msg, "stop_reason", None)
    if stop == "max_tokens" and calls:
        log.warning("The model reached max_tokens while writing a tool call; dropping %s", calls[-1]["name"])
        calls.pop()
    return text, calls, usage_dict(getattr(msg, "usage", None)), stop


def complete(messages: List[dict], *, max_tokens: int, timeout: float, tools: Optional[List[dict]] = None) -> dict:
    """One blocking request: {"text", "tool_calls", "usage", "finish_reason"}. Raises the SDK's own errors."""
    params = build_params(messages, max_tokens=max_tokens, tools=tools)
    msg = client(timeout).messages.create(**params)
    text, calls, usage, stop = parse_message(msg)
    return {"text": text, "tool_calls": calls, "usage": usage, "finish_reason": stop}


def stream(messages: List[dict], *, max_tokens: int, timeout: float, info: dict,
           tools: Optional[List[dict]] = None) -> Iterator[str]:
    """Yield the text deltas of a streamed request. When it ends, info["usage"], info["tool_calls"] (as complete() returns
    them) and info["finish_reason"] are set. Closing the generator closes the connection. Raises the SDK's own errors."""
    params = build_params(messages, max_tokens=max_tokens, tools=tools)
    with client(timeout).messages.stream(**params) as s:
        for piece in s.text_stream:
            if piece:
                yield piece
        final = s.get_final_message()
    _text, calls, usage, stop = parse_message(final)
    info["usage"], info["tool_calls"], info["finish_reason"] = usage, calls, stop


def error_text(e: BaseException) -> str:
    """One plain sentence for a failed request, from the SDK's typed errors (never by searching the message)."""
    if llm is not None:
        if isinstance(e, llm.APITimeoutError):
            return "the language model did not answer in time"
        if isinstance(e, llm.APIConnectionError):
            return "could not reach the language model (connection error)"
        if isinstance(e, llm.RateLimitError):
            return "language model rate limit reached (HTTP 429)"
        if isinstance(e, llm.APIStatusError):
            return f"the language model answered HTTP {e.status_code}: {str(getattr(e, 'message', e))[:200]}"
    return f"{type(e).__name__}: {str(e)[:200]}"
