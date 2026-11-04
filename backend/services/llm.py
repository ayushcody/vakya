"""The language model: one chat-completion client for every model call in Vakya (replies, tool turns, side tasks).

It speaks the OpenAI-compatible chat API of a hosted model, by default Google's Gemini Flash. Settings (backend/.env):
  LLM_BASE_URL  default https://generativelanguage.googleapis.com/v1beta/openai/
  LLM_MODEL     default the stable Gemini Flash (services/gemini.py)
  LLM_API_KEY   the key; GEMINI_API_KEY is accepted too
Without a key nothing is sent and nothing raises on the spoken path: `chat(..., no_key_line=True)` and
`chat_stream(..., no_key_line=True)` return the fixed line `no_key_reply(language)` ("Needs a Gemini key" is what
`status()` reports), so a typed web-call turn works end to end. Side-task callers leave `no_key_line` off and get
LLMUnavailable, which they already treat as "skip this helper".

Gemini's OpenAI-compatible endpoint (read 4 Nov 2026; the same notes are in services/gemini.py):
  * https://ai.google.dev/gemini-api/docs/openai  base URL, chat, streaming (stream_options include_usage), tools in
    the OpenAI format with tool_choice "auto" (the only value documented; nothing is said about parallel tool calls, so a
    reply may carry one or several tool_calls and the caller loops over them), system message first,
    reasoning_effort (minimal, low, medium, high). Streamed tool-call deltas are not described: they are merged here by
    `index`, tolerating a chunk with no index or an empty id (an id like call_<n> is made up and used on both sides).
  * https://ai.google.dev/gemini-api/docs/models  stable and preview Flash model ids.
So a request carries only model, messages, temperature, max_tokens, stream, stream_options, tools, tool_choice and,
for Gemini, reasoning_effort (LLM_REASONING_EFFORT, default "low": reasoning tokens are counted in max_tokens, so a
small reply budget would otherwise be spent thinking). JSON replies are asked for in the prompt and read with
`extract_json` (json_object response_format is not documented for this endpoint).

Requests go through one pooled keep-alive HTTP client (`_urlopen`, httpx), so the TCP and TLS handshake is paid once, not
on every reply; `prewarm()` opens that connection ahead of time (a phone starts ringing). A 429 or 5xx is retried a
couple of times with a short backoff (Retry-After or retryDelay when given; LLM_RETRIES, LLM_RETRY_MAX_WAIT_S).

Callers use `chat()` (blocking; run it with asyncio.to_thread) and get the text, the model and any tool calls.
`chat_stream()` is the same over the streaming API (SSE): it yields text deltas as they are generated so a caller can
start speaking the first sentence while the rest is still being written.
"""
import atexit
import io
import itertools
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, Iterator, List, Optional

try:
    import httpx
except ImportError:  # the pooled client needs httpx (in requirements.txt); without it every call opens its own connection
    httpx = None

try:
    from core import offline
    from services import gemini
except ImportError:  # pragma: no cover - running from a different cwd
    from backend.core import offline
    from backend.services import gemini

log = logging.getLogger("vakya.llm")

PROVIDER = "openai-compatible"
NO_KEY_PROVIDER = "no-key"          # the provider name of a fixed no-key line
NEEDS_KEY_MESSAGE = "Needs a Gemini key"


class LLMUnavailable(RuntimeError):
    """No answer could be had. `reasons` maps provider -> why it was skipped or failed."""

    def __init__(self, reasons: Dict[str, str]):
        self.reasons = reasons
        super().__init__("No language model is available: " + "; ".join(f"{k}: {v}" for k, v in reasons.items()))


@dataclass
class ChatResult:
    text: str
    provider: str
    model: str
    latency_ms: int
    usage: Dict[str, int] = field(default_factory=dict)
    # [{"id", "name", "arguments": dict}] when the model asked for tools (plus "extra_content" when the server sent one,
    # which must go back with the call: see tool_call_message)
    tool_calls: List[dict] = field(default_factory=list, kw_only=True)


@dataclass
class StreamResult(ChatResult):
    """What a finished stream summarises: the full text, which provider answered and how long it took."""
    first_token_ms: Optional[int] = None


class LLMStreamInterrupted(RuntimeError):
    """The model failed after it had already produced text. `partial` is what arrived before the failure (the caller has
    already seen, and maybe spoken, part of the reply, so the request is not made again)."""

    def __init__(self, provider: str, model: str, partial: str, cause: BaseException):
        self.provider, self.model, self.partial, self.cause = provider, model, partial, cause
        super().__init__(f"{provider} stopped mid-reply after {len(partial)} characters: {type(cause).__name__}: "
                         f"{str(cause)[:200]}")


# ---------------------------------------------------------------------------------------------- configuration

def _unavailable_reason() -> Optional[str]:
    if offline.enabled() and not offline.is_local_url(gemini.base_url()):
        return offline.reason("the hosted language model")
    if not gemini.llm_configured():
        return NEEDS_KEY_MESSAGE
    return None


def is_unconfigured(e: BaseException) -> bool:
    """True when `e` is the LLMUnavailable of a model that is not connected (no key, or offline mode), as opposed to one
    that failed: the turn then says `no_key_reply` instead of its generic fallback line."""
    if not isinstance(e, LLMUnavailable):
        return False
    return any(r == NEEDS_KEY_MESSAGE or str(r).startswith("Offline mode") for r in e.reasons.values())


def configured() -> bool:
    """True when a key is set (and offline mode does not block the hosted model)."""
    return _unavailable_reason() is None


# The latest error of a request that failed, cleared when the next one succeeds (a failing model is otherwise invisible
# to anyone who only sees the fallback line).
last_failures: Dict[str, str] = {}


def status() -> dict:
    """The model this backend talks to and whether it can: {"provider", "base_url", "model", "configured", "reason"}."""
    reason = _unavailable_reason()
    return {"provider": PROVIDER, "base_url": gemini.base_url(), "model": gemini.chat_model(),
            "configured": reason is None, "reason": reason}


_NO_KEY_LINES = {
    "en": "Sorry, the assistant is not connected yet. Please try again shortly.",
    "hi": "क्षमा करें, सहायक अभी जुड़ा नहीं है। कृपया थोड़ी देर बाद फिर कोशिश करें।",
    "hinglish": "Maaf kijiye, assistant abhi connect nahi hua hai. Kripya thodi der baad phir koshish karein.",
    "mr": "क्षमस्व, सहाय्यक अजून जोडलेला नाही. कृपया थोड्या वेळाने पुन्हा प्रयत्न करा.",
    "bn": "দুঃখিত, সহকারী এখনও সংযুক্ত হয়নি। অনুগ্রহ করে একটু পরে আবার চেষ্টা করুন।",
    "ta": "மன்னிக்கவும், உதவியாளர் இன்னும் இணைக்கப்படவில்லை. சிறிது நேரம் கழித்து மீண்டும் முயற்சிக்கவும்.",
    "te": "క్షమించండి, అసిస్టెంట్ ఇంకా కనెక్ట్ కాలేదు. దయచేసి కొంచెం సేపటి తర్వాత మళ్లీ ప్రయత్నించండి.",
    "kn": "ಕ್ಷಮಿಸಿ, ಸಹಾಯಕ ಇನ್ನೂ ಸಂಪರ್ಕಗೊಂಡಿಲ್ಲ. ದಯವಿಟ್ಟು ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
    "ml": "ക്ഷമിക്കണം, അസിസ്റ്റന്റ് ഇതുവരെ കണക്റ്റ് ചെയ്തിട്ടില്ല. ദയവായി അൽപ്പം കഴിഞ്ഞ് വീണ്ടും ശ്രമിക്കുക.",
    "gu": "માફ કરશો, સહાયક હજી જોડાયેલ નથી. કૃપા કરીને થોડીવાર પછી ફરી પ્રયાસ કરો.",
    "pa": "ਮਾਫ਼ ਕਰਨਾ, ਸਹਾਇਕ ਹਾਲੇ ਜੁੜਿਆ ਨਹੀਂ ਹੈ। ਕਿਰਪਾ ਕਰਕੇ ਥੋੜ੍ਹੀ ਦੇਰ ਬਾਅਦ ਦੁਬਾਰਾ ਕੋਸ਼ਿਸ਼ ਕਰੋ।",
}


def no_key_reply(language: Optional[str] = None) -> str:
    """The fixed line said when no model is connected, in the caller's language (English for any other)."""
    code = re.split(r"[-_]", (language or "en").strip().lower())[0]
    return _NO_KEY_LINES.get(code, _NO_KEY_LINES["en"])


def answer_temperature() -> float:
    """LLM_ANSWER_TEMPERATURE (default 0.2, clamped to 0..1): sampling temperature of a grounded spoken answer. Low, so
    the model copies a time or a fee from the passages instead of paraphrasing it."""
    try:
        return min(1.0, max(0.0, float(os.getenv("LLM_ANSWER_TEMPERATURE", "0.2"))))
    except ValueError:
        return 0.2


_effort_rejected = False  # the server answered 400 to reasoning_effort once: stop sending it


def reasoning_effort() -> str:
    """LLM_REASONING_EFFORT (minimal | low | medium | high | off), default "low" on Google's endpoint and off elsewhere."""
    if _effort_rejected:
        return ""
    raw = (os.getenv("LLM_REASONING_EFFORT") or ("low" if gemini.uses_gemini() else "")).strip().lower()
    return "" if raw in ("", "off", "none", "0", "false", "no") else raw


def _with_reasoning_room(max_tokens: int, effort: str) -> int:
    """Reasoning tokens are counted in max_tokens: a reply that reasons first gets REASONING_TOKEN_ROOM (default 1024)
    on top of what the answer needs, or a short reply budget would be spent before the first word."""
    if not effort:
        return max_tokens
    try:
        room = int(os.getenv("REASONING_TOKEN_ROOM", "1024"))
    except ValueError:
        room = 1024
    return max_tokens + max(0, room)


# ---------------------------------------------------------------------------------------------- usage meter

_usage_lock = threading.Lock()
_usage: Dict[str, Dict[str, int]] = {}
_usage_day = date.today()


def _record_usage(provider: str, usage: Dict[str, int]) -> None:
    global _usage_day
    with _usage_lock:
        if date.today() != _usage_day:
            _usage.clear()
            _usage_day = date.today()
        row = _usage.setdefault(provider, {"requests": 0, "tokens": 0})
        row["requests"] += 1
        row["tokens"] += int(usage.get("total_tokens") or 0)


def usage_today() -> Dict[str, Dict[str, int]]:
    with _usage_lock:
        return {k: dict(v) for k, v in _usage.items()}


# ---------------------------------------------------------------------------------------------- HTTP helper

USER_AGENT = "Vakya/1.0 (+https://github.com/ayushcody/vakya)"

# One keep-alive connection pool for every request. urllib opens a new connection (TCP + TLS, about 70-100 ms) for each
# call and closes it; httpx keeps idle connections for reuse. A stale one (closed by the server while idle) is detected
# before it is reused, and a request that dies on a reused connection before the server answered is sent once more on a
# fresh one.
POOL_KEEPALIVE_S = 120.0
_pool_lock = threading.Lock()
_pool = None


def _client():
    """The shared httpx client (created on first use), or None when httpx is not installed."""
    global _pool
    if httpx is None:
        return None
    with _pool_lock:
        if _pool is None:
            _pool = httpx.Client(limits=httpx.Limits(max_connections=32, max_keepalive_connections=8,
                                                     keepalive_expiry=POOL_KEEPALIVE_S),
                                 follow_redirects=False)
        return _pool


def close_pool() -> None:
    """Drop every pooled connection (the next request opens a new one)."""
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        try:
            pool.close()
        except Exception:  # noqa: BLE001
            pass


atexit.register(close_pool)


def _translate(e: BaseException, timeout: Optional[float]) -> BaseException:
    """An httpx failure as the exception urlopen would have raised."""
    if isinstance(e, httpx.TimeoutException):
        return TimeoutError(f"{type(e).__name__}: timed out after {timeout} s")
    return urllib.error.URLError(e)


class _Response:
    """What a request returns, shaped like urllib's response: a context manager, `.read()`, iterable by raw lines."""

    def __init__(self, resp, timeout: Optional[float]):
        self._resp, self._timeout = resp, timeout
        self._lines = None
        self.status = resp.status_code
        self.headers = resp.headers

    def read(self) -> bytes:
        try:
            return self._resp.read()
        except httpx.HTTPError as e:
            raise _translate(e, self._timeout) from e

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        if self._lines is None:
            self._lines = self._resp.iter_lines()
        try:
            return next(self._lines).encode("utf-8") + b"\n"
        except httpx.HTTPError as e:
            raise _translate(e, self._timeout) from e

    def close(self) -> None:
        self._resp.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _urlopen(req: "urllib.request.Request", timeout: Optional[float] = None):
    """Send a urllib Request through the pooled client. Raises urllib.error.HTTPError for an HTTP error status (its
    body readable, like urlopen's), TimeoutError for a timeout and urllib.error.URLError for other connection problems,
    so callers handle it exactly as they handled urlopen. The response body is read lazily: `.read()` reads all of it,
    iterating yields the lines as they arrive (an event stream)."""
    client = _client()
    if client is None:
        return urllib.request.urlopen(req, timeout=timeout)
    headers = {k: v for k, v in req.header_items()}
    headers.setdefault("Accept-Encoding", "identity")  # a compressed event stream may be buffered on its way
    limit = httpx.Timeout(timeout) if timeout else httpx.Timeout(None)
    request = client.build_request(req.get_method(), req.full_url, content=req.data, headers=headers, timeout=limit)
    resp = None
    for attempt in (0, 1):
        try:
            resp = client.send(request, stream=True)
            break
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError) as e:
            if attempt == 0:  # a pooled connection that the server had closed: once more on a new one
                log.info("pooled connection was stale (%s); retrying on a new one", type(e).__name__)
                continue
            raise _translate(e, timeout) from e
        except httpx.HTTPError as e:
            raise _translate(e, timeout) from e
    if resp.status_code >= 400:
        try:
            body = resp.read()
        except httpx.HTTPError as e:
            raise _translate(e, timeout) from e
        finally:
            resp.close()
        raise urllib.error.HTTPError(req.full_url, resp.status_code, resp.reason_phrase, dict(resp.headers),
                                     io.BytesIO(body))
    return _Response(resp, timeout)


class ProviderHTTPError(RuntimeError):
    """An HTTP error status from the server. `str()` is "HTTP <code>: <first 300 characters of the body>"; the whole body
    and the response headers are kept for reading a retry delay."""

    def __init__(self, code: int, body: str, headers: Any = None):
        self.code, self.body = int(code), body or ""
        try:
            self.headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        except AttributeError:  # pragma: no cover - an unusual headers object
            self.headers = {}
        super().__init__(f"HTTP {self.code}: {self.body[:300]}")


# ---- retry: a 429 or 5xx is tried again a couple of times, as soon as the server says it may be (Retry-After, Gemini's
# retryDelay, "try again in 1.6s"), else after 0.5 s, 1 s. A wait longer than LLM_RETRY_MAX_WAIT_S is not taken (a spoken
# turn cannot hold the line that long): the error goes to the caller, which says its fallback line.
RETRY_STATUS = (429, 500, 502, 503, 504)
_sleep = time.sleep
_DELAY_RE = re.compile(r'"retryDelay"\s*:\s*"([\d.]+)s"|try again in\s+([\d.]+)\s*(ms|s)\b', re.IGNORECASE)


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _retry_wait(e: ProviderHTTPError, attempt: int) -> Optional[float]:
    """Seconds to wait before retrying after `e` (the attempt just made was number `attempt`, from 0), None to give up."""
    if e.code not in RETRY_STATUS or attempt >= int(max(0, _env_number("LLM_RETRIES", 2))):
        return None
    wait: Optional[float] = None
    try:
        wait = float(e.headers.get("retry-after", ""))
    except ValueError:
        pass
    if wait is None:
        m = _DELAY_RE.search(e.body or "")
        if m:
            wait = float(m.group(1)) if m.group(1) else float(m.group(2)) / (1000.0 if m.group(3).lower() == "ms" else 1.0)
    if wait is None:
        wait = 0.5 * (2 ** attempt)
    return wait if wait <= _env_number("LLM_RETRY_MAX_WAIT_S", 3.0) else None


def _send(url: str, body: dict, headers: dict, timeout: Optional[float]) -> dict:
    """POST a (non-streaming) JSON request, retrying a 429 or 5xx; any other HTTP error status raises ProviderHTTPError."""
    attempt = 0
    while True:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        try:
            with _urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            err = ProviderHTTPError(e.code, e.read().decode(errors="replace"), e.headers)
            wait = _retry_wait(err, attempt)
            if wait is None:
                raise err from e
            log.warning("language model answered HTTP %s; trying again in %.1f s", e.code, wait)
            _sleep(wait)
            attempt += 1


def _post_stream(url: str, body: dict, headers: dict, timeout: float):
    """Open a streaming POST; a 429 or 5xx is retried, and a server that rejects `stream_options` (or `reasoning_effort`)
    gets one retry without it. Any other HTTP error status raises ProviderHTTPError."""
    attempt = 0
    while True:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        try:
            return _urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            full = e.read().decode(errors="replace")
            err = ProviderHTTPError(e.code, full, e.headers)
            if e.code in (400, 422) and "stream_options" in body and "stream_options" in full:
                body = {k: v for k, v in body.items() if k != "stream_options"}
                continue
            if e.code in (400, 422) and "reasoning_effort" in body and "reasoning" in full.lower():
                _reject_effort()
                body = {k: v for k, v in body.items() if k != "reasoning_effort"}
                continue
            wait = _retry_wait(err, attempt)
            if wait is None:
                raise err from e
            log.warning("language model answered HTTP %s; trying again in %.1f s", e.code, wait)
            _sleep(wait)
            attempt += 1


def _reject_effort() -> None:
    global _effort_rejected
    _effort_rejected = True
    log.warning("the language model does not take reasoning_effort; sending requests without it from now on")


# ---------------------------------------------------------------------------------------------- messages and tools

PLAIN_TEXT_NOTE = ("(Answer the caller now in plain spoken text. Do not call a tool or function and do not write "
                   "JSON.)")
_call_ids = itertools.count(1)


def _new_call_id() -> str:
    """An id for a tool call the model sent without one (used in the assistant message and in the tool result)."""
    return f"call_{next(_call_ids)}"


def prepare_messages(messages: List[dict]) -> List[dict]:
    """The messages as the endpoint wants them: one system message, first. A system message that came later (the JSON-only
    note) is joined onto the first. Assistant messages with tool_calls and tool results are left as they are."""
    out: List[dict] = []
    system: List[str] = []
    for m in messages:
        if m.get("role") == "system" and isinstance(m.get("content"), str):
            system.append(m["content"])
        else:
            out.append(m)
    if not system:
        return list(messages)
    return [{"role": "system", "content": "\n\n".join(system)}, *out]


def _flatten_tool_messages(messages: List[dict]) -> List[dict]:
    """The same conversation with tool calls and results written as plain text, for a request that carries no tools."""
    out: List[dict] = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = "; ".join(f"{(c.get('function') or {}).get('name')}({(c.get('function') or {}).get('arguments')})"
                              for c in m["tool_calls"])
            text = ((m.get("content") or "") + f" [called: {calls}]").strip()
            out.append({"role": "assistant", "content": text})
        elif m.get("role") == "tool":
            out.append({"role": "user", "content": f"Tool result: {m.get('content')}"})
        else:
            out.append(m)
    return out


def _plain_text_messages(messages: List[dict]) -> List[dict]:
    """`messages` with the tool history as plain text and PLAIN_TEXT_NOTE added to the caller's last message: the final
    round of a tool turn (the model must answer now), asked without `tools`."""
    out = [dict(m) for m in _flatten_tool_messages(messages)]
    if out and out[-1].get("role") == "user":
        out[-1]["content"] = f"{out[-1].get('content') or ''}\n\n{PLAIN_TEXT_NOTE}"
    else:
        out.append({"role": "user", "content": PLAIN_TEXT_NOTE})
    return out


def _parse_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        args = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return args if isinstance(args, dict) else {}


def _tool_calls(message: dict) -> List[dict]:
    """The tool calls of an OpenAI-style assistant message (one or several), with the arguments parsed ({} when they are
    not JSON). A call with no id gets a made-up one."""
    out = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        call = {"id": tc.get("id") or _new_call_id(), "name": fn.get("name") or "", "arguments": _parse_args(fn.get("arguments"))}
        if tc.get("extra_content"):
            call["extra_content"] = tc["extra_content"]
        out.append(call)
    return out


def tool_call_message(text: Optional[str], tool_calls: List[dict]) -> dict:
    """The assistant message to put back in the conversation for `tool_calls` (a ChatResult's list), carrying every
    id, name and the arguments as JSON, and an `extra_content` the server attached to a call (Gemini's reasoning
    signature), so the next request repeats what the model said. Answer each call with
    {"role": "tool", "tool_call_id": <the same id>, "content": ...}."""
    calls = []
    for tc in tool_calls:
        item = {"id": tc["id"], "type": "function",
                "function": {"name": tc["name"], "arguments": json.dumps(tc.get("arguments") or {}, ensure_ascii=False)}}
        if tc.get("extra_content"):
            item["extra_content"] = tc["extra_content"]
        calls.append(item)
    return {"role": "assistant", "content": text or None, "tool_calls": calls}


class _ToolDeltas:
    """Merges the `tool_calls` deltas of a streamed reply into whole calls. OpenAI-style chunks carry an `index` (and the
    id and name on the first one, argument fragments on the rest); Gemini may send a whole call in one chunk, with no
    index or an empty id. A delta with an index belongs to that slot; one without belongs to a new call when it names a
    function or carries an id, else to the call before it."""

    def __init__(self) -> None:
        self.slots: Dict[Any, dict] = {}
        self._order: List[Any] = []
        self._last: Any = None

    def feed(self, deltas: Iterable[dict]) -> None:
        for d in deltas or []:
            if not isinstance(d, dict):
                continue
            fn = d.get("function") or {}
            index = d.get("index")
            if index is None:
                new = bool(d.get("id") or fn.get("name")) or self._last is None
                key = (f"seq{len(self._order)}") if new else self._last
            else:
                key = index
            slot = self.slots.get(key)
            if slot is None:
                slot = self.slots[key] = {"id": "", "name": "", "args": "", "extra": None}
                self._order.append(key)
            self._last = key
            if d.get("id"):
                slot["id"] = d["id"]
            if fn.get("name"):
                slot["name"] = fn["name"]
            args = fn.get("arguments")
            if isinstance(args, dict):
                slot["args"] = json.dumps(args, ensure_ascii=False)
            elif args:
                slot["args"] += args
            if d.get("extra_content"):
                slot["extra"] = d["extra_content"]

    def calls(self) -> List[dict]:
        out = []
        for key in self._order:
            s = self.slots[key]
            if not s["name"]:
                continue
            call = {"id": s["id"] or _new_call_id(), "name": s["name"], "arguments": _parse_args(s["args"])}
            if s["extra"]:
                call["extra_content"] = s["extra"]
            out.append(call)
        return out


# ---------------------------------------------------------------------------------------------- requests

def _request(model: str, messages: List[dict], *, temperature: float, max_tokens: int, stream: bool,
             tools: Optional[List[dict]] = None, stop: Optional[List[str]] = None) -> dict:
    effort = reasoning_effort()
    body: Dict[str, Any] = {"model": model, "messages": prepare_messages(messages), "temperature": temperature,
                            "max_tokens": _with_reasoning_room(max_tokens, effort), "stream": stream}
    if stream:
        body["stream_options"] = {"include_usage": True}
    if effort:
        body["reasoning_effort"] = effort
    if stop:
        body["stop"] = stop
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"  # the only tool_choice the endpoint documents
    return body


def _headers(stream: bool) -> dict:
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if stream:
        headers["Accept"] = "text/event-stream"
    key = gemini.api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _url() -> str:
    return gemini.base_url().rstrip("/") + "/chat/completions"


def _openai_chat(model: str, messages: List[dict], *, temperature: float, max_tokens: int, timeout: float,
                 tools: Optional[List[dict]] = None) -> dict:
    """One chat completion (the parsed response). A server that rejects `reasoning_effort` is asked again without it."""
    body = _request(model, messages, temperature=temperature, max_tokens=max_tokens, stream=False, tools=tools)
    try:
        return _send(_url(), body, _headers(False), timeout)
    except ProviderHTTPError as e:
        if e.code in (400, 422) and "reasoning_effort" in body and "reasoning" in e.body.lower():
            _reject_effort()
            body.pop("reasoning_effort")
            return _send(_url(), body, _headers(False), timeout)
        raise


def _chat_result(data: dict, model: str, started: float) -> ChatResult:
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("the model returned no choices")
    message = choices[0].get("message") or {}
    return ChatResult(message.get("content") or "", PROVIDER, model, int((time.time() - started) * 1000),
                      data.get("usage") or {}, tool_calls=_tool_calls(message))


def _openai_stream(model: str, messages: List[dict], *, temperature: float, max_tokens: int, timeout: float, info: dict,
                   tools: Optional[List[dict]] = None) -> Iterator[str]:
    """Yield the text deltas of a streamed chat completion (`data:` server-sent-event lines).

    `timeout` bounds each wait on the socket (connect and every read), so a stalled server is noticed. Token usage,
    when the server reports it, lands in info["usage"], and the tool calls the model made in info["tool_calls"]."""
    body = _request(model, messages, temperature=temperature, max_tokens=max_tokens, stream=True, tools=tools)
    resp = _post_stream(_url(), body, _headers(True), timeout)
    deltas = _ToolDeltas()
    with resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):  # blank separators, ": keep-alive" comments, "event:" lines
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                # Read to the end of the body (only its closing bytes are left) so the connection goes back to the
                # pool: a response closed unfinished costs the next reply a new TCP + TLS handshake.
                try:
                    for _ in resp:
                        pass
                except Exception:  # noqa: BLE001 - the reply is complete whatever happens to its closing bytes
                    pass
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, list):  # an error may arrive as a one-item array
                obj = obj[0] if obj and isinstance(obj[0], dict) else None
            if not isinstance(obj, dict):
                continue
            if obj.get("error"):
                err = obj["error"]
                raise RuntimeError(str(err.get("message") if isinstance(err, dict) else err)[:300])
            usage = obj.get("usage")
            if isinstance(usage, dict) and usage:
                info["usage"] = usage
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("tool_calls"):
                    deltas.feed(delta["tool_calls"])
                piece = delta.get("content")
                if piece:
                    yield piece
    info["tool_calls"] = deltas.calls()


# ---------------------------------------------------------------------------------------------- public API

def _no_key_result(language: Optional[str]) -> ChatResult:
    return ChatResult(no_key_reply(language), NO_KEY_PROVIDER, "", 0)


def _fail(e: BaseException) -> LLMUnavailable:
    last_failures[PROVIDER] = f"failed: {type(e).__name__}: {str(e)[:200]}"
    return LLMUnavailable({PROVIDER: last_failures[PROVIDER]})


def chat(messages: List[dict], *, json_mode: bool = False, temperature: float = 0.3, max_tokens: int = 400,
         timeout: float = 30.0, tools: Optional[List[dict]] = None, tool_choice: Optional[str] = None,
         json_tools: Optional[Iterable[str]] = None, no_key_line: bool = False,
         language: Optional[str] = None) -> ChatResult:
    """Ask the model. In JSON mode the reply text is a JSON object string.

    With `tools` (OpenAI function definitions) the request carries them with tool_choice "auto" and the result's
    `tool_calls` lists what the model asked for, one or several. `tool_choice="none"` is the final round of a tool turn:
    the request goes out without tools, the tool history written as plain text and a note to answer in words.

    Without a key: `no_key_line=True` returns the fixed line `no_key_reply(language)` (provider "no-key"); otherwise
    LLMUnavailable is raised, which a side task takes as "skip". Any failure of the request raises LLMUnavailable.

    `json_tools` is accepted and ignored (it served a provider that rejected native tool calls)."""
    reason = _unavailable_reason()
    if reason:
        if no_key_line:
            return _no_key_result(language)
        raise LLMUnavailable({PROVIDER: reason})
    if json_mode and not any("json" in (m.get("content") or "").lower() for m in messages if isinstance(m.get("content"), str)):
        messages = [*messages, {"role": "system", "content": "Respond with a single JSON object only."}]
    if tools and tool_choice == "none":
        messages, tools = _plain_text_messages(messages), None
    model = gemini.chat_model()
    t = time.time()
    try:
        res = _chat_result(_openai_chat(model, messages, temperature=temperature, max_tokens=max_tokens,
                                        timeout=timeout, tools=tools), model, t)
        if json_mode:
            res.text = json.dumps(extract_json(res.text))
    except Exception as e:  # noqa: BLE001
        log.warning("language model request failed: %s", e)
        raise _fail(e) from e
    _record_usage(PROVIDER, res.usage)
    last_failures.pop(PROVIDER, None)
    return res


class ChatStream:
    """Iterate to receive text deltas; afterwards `.result` is the summary (StreamResult).

    The request is made on the first `next()`. A failure before any text raises LLMUnavailable; one after it raises
    LLMStreamInterrupted (with the partial text). `provider` / `model` are set as soon as the first delta arrives.
    `tool_calls` lists the calls the model made (when `tools` were passed). `info` is the live request state."""

    def __init__(self, messages: List[dict], *, temperature: float, max_tokens: int, timeout: float,
                 total_timeout: Optional[float], tools: Optional[List[dict]] = None, no_key_line: bool = False,
                 language: Optional[str] = None):
        self.messages, self.temperature, self.max_tokens = messages, temperature, max_tokens
        self.tools = tools
        self.no_key_line, self.language = no_key_line, language
        self.timeout, self.total_timeout = timeout, total_timeout
        self.provider: Optional[str] = None
        self.model: Optional[str] = None
        self.first_token_ms: Optional[int] = None
        self.text = ""
        self.tool_calls: List[dict] = []
        self.result: Optional[StreamResult] = None
        self.info: dict = {}
        self._gen: Optional[Iterator[str]] = None

    def __iter__(self) -> Iterator[str]:
        if self._gen is None:
            self._gen = self._run()
        return self._gen

    def close(self) -> None:
        """Stop early (the consumer went away); releases the connection."""
        if self._gen is not None:
            self._gen.close()

    def _run(self) -> Iterator[str]:
        reason = _unavailable_reason()
        if reason:
            if not self.no_key_line:
                raise LLMUnavailable({PROVIDER: reason})
            line = no_key_reply(self.language)
            self.provider, self.model, self.first_token_ms, self.text = NO_KEY_PROVIDER, "", 0, line
            self.result = StreamResult(line, NO_KEY_PROVIDER, "", 0, {}, 0)
            yield line
            return
        info: dict = {}
        self.info = info
        model = gemini.chat_model()
        info["model"] = model
        t0 = time.time()
        deadline = t0 + self.total_timeout if self.total_timeout else None
        produced = ""
        stream = _openai_stream(model, self.messages, temperature=self.temperature, max_tokens=self.max_tokens,
                                timeout=self.timeout, info=info, tools=self.tools)
        try:
            for piece in stream:
                if not produced:
                    self.provider, self.model = PROVIDER, model
                    self.first_token_ms = int((time.time() - t0) * 1000)
                produced += piece
                self.text = produced
                yield piece
                if deadline and time.time() > deadline:
                    raise TimeoutError(f"reply not finished within {self.total_timeout:g} s")
        except GeneratorExit:  # the consumer stopped listening
            stream.close()
            raise
        except Exception as e:  # noqa: BLE001
            stream.close()
            if produced:
                log.warning("language model stopped mid-reply: %s", e)
                raise LLMStreamInterrupted(PROVIDER, model, produced, e) from e
            log.warning("language model failed to stream: %s", e)
            raise _fail(e) from e
        usage = info.get("usage") or {}
        self.tool_calls = info.get("tool_calls") or []
        if produced or self.tool_calls:
            self.provider, self.model = PROVIDER, model
            if self.first_token_ms is None:
                self.first_token_ms = int((time.time() - t0) * 1000)
            self.result = StreamResult(produced, PROVIDER, model, int((time.time() - t0) * 1000), usage,
                                       self.first_token_ms, tool_calls=self.tool_calls)
            _record_usage(PROVIDER, usage)
            last_failures.pop(PROVIDER, None)
            return
        log.warning("language model streamed an empty reply")
        last_failures[PROVIDER] = "failed: empty reply"
        raise LLMUnavailable({PROVIDER: "failed: empty reply"})


def chat_stream(messages: List[dict], *, temperature: float = 0.3, max_tokens: int = 400, timeout: float = 30.0,
                total_timeout: Optional[float] = None, json_tools: Optional[Iterable[str]] = None,
                tools: Optional[List[dict]] = None, no_key_line: bool = False,
                language: Optional[str] = None) -> ChatStream:
    """Like `chat()`, but streamed: iterate the returned ChatStream for text deltas (blocking; run it in a thread).

    `timeout` is the longest wait for the next bytes; `total_timeout` (optional) caps the whole reply. After the
    iteration `.result` holds provider, model, latency, usage, the time to the first token and any `tool_calls`
    (merged from the streamed deltas). With no key, `no_key_line=True` streams the fixed line as one chunk. `json_tools`
    is accepted and ignored."""
    return ChatStream(messages, temperature=temperature, max_tokens=max_tokens, timeout=timeout,
                      total_timeout=total_timeout, tools=tools, no_key_line=no_key_line, language=language)


def extract_json(text: str) -> dict:
    """Parse a JSON object from a model reply, tolerating code fences or prose around it."""
    text = (text or "").strip()
    try:
        out = json.loads(text)
        if isinstance(out, dict):
            return out
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("model reply contained no JSON object")
    return json.loads(m.group(0))


# ---------------------------------------------------------------------------------------------- connection prewarm

PREWARM_MIN_INTERVAL_S = 60.0
_prewarm_lock = threading.Lock()
_last_prewarm: Optional[float] = None


def _origin(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/"


def _prewarm_targets() -> List[str]:
    """The origins a reply will connect to right now: the model's, when it is configured."""
    return [_origin(gemini.base_url())] if configured() else []


def _prewarm_now() -> None:
    client = _client()
    if client is None:
        return
    for url in _prewarm_targets():
        started = time.time()
        try:
            # A HEAD to the origin: no key is sent and no token is spent, the answer (usually a 404) does not matter. It
            # leaves a TCP + TLS connection in the pool, which the first real request of a reply then reuses.
            client.head(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
                        timeout=httpx.Timeout(5.0))
            log.info("LLM connection to %s warm (%d ms)", url, int((time.time() - started) * 1000))
        except Exception as e:  # noqa: BLE001 - a warm-up must never matter
            log.info("LLM connection prewarm of %s failed: %s", url, type(e).__name__)


def prewarm(*, wait: bool = False) -> None:
    """Open the connection to the hosted language model ahead of the first reply (a phone starts ringing, the backend
    starts), so the reply does not pay the TCP + TLS handshake.

    Cheap (one HEAD request, no key, no tokens), never raises, and at most once per PREWARM_MIN_INTERVAL_S (a call
    inside that window does nothing). It returns at once: the request is made on a short-lived daemon thread, so it is
    safe to call straight from async code (or through asyncio.to_thread). `wait=True` makes the request on the calling
    thread and returns when it is done (the startup warm-up uses it to time the step)."""
    global _last_prewarm
    try:
        with _prewarm_lock:
            now = time.monotonic()
            if _last_prewarm is not None and now - _last_prewarm < PREWARM_MIN_INTERVAL_S:
                return
            _last_prewarm = now
        if wait:
            _prewarm_now()
        else:
            threading.Thread(target=_prewarm_now, name="llm-prewarm", daemon=True).start()
    except Exception as e:  # noqa: BLE001
        log.info("LLM prewarm skipped: %s", e)
