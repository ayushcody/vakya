"""Shared pieces of the language-model tests: the environment of an OpenAI-compatible endpoint, and a local HTTP server
that plays it (canned responses, request bodies recorded). Nothing here touches the network beyond 127.0.0.1."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from services import llm

MSGS = [{"role": "user", "content": "hi"}]


def use_endpoint(monkeypatch, url=None, key="secret", model="tiny", effort=None):
    """Point services/llm.py at `url` with a key. `effort`: LLM_REASONING_EFFORT (None leaves the default)."""
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "GEMINI_API_KEY", "LLM_MODEL", "LLM_REASONING_EFFORT", "LLM_RETRIES",
                 "LLM_RETRY_MAX_WAIT_S", "VAKYA_OFFLINE"):
        monkeypatch.delenv(name, raising=False)
    if url:
        monkeypatch.setenv("LLM_BASE_URL", url)
    if key:
        monkeypatch.setenv("LLM_API_KEY", key)
    if model:
        monkeypatch.setenv("LLM_MODEL", model)
    if effort is not None:
        monkeypatch.setenv("LLM_REASONING_EFFORT", effort)
    monkeypatch.setattr(llm, "_effort_rejected", False)
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    llm.last_failures.clear()


def sse(*events) -> bytes:
    out = b""
    for e in events:
        out += (e if isinstance(e, str) else "data: " + json.dumps(e)).encode() + b"\n\n"
    return out


def chunk(text=None, **extra):
    delta = {} if text is None else {"content": text}
    return {"choices": [{"index": 0, "delta": delta}], **extra}


def reply(message=None, **extra) -> bytes:
    return json.dumps({"choices": [{"message": message or {"content": "OK"}}], "usage": {"total_tokens": 2},
                       **extra}).encode()


class FakeServer:
    """Serves one canned (status, body) per POST, the last one again when they run out; records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
                status, payload = outer.responses.pop(0) if len(outer.responses) > 1 else outer.responses[0]
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream" if status == 200 and payload[:5] == b"data:"
                                 else "application/json")
                self.end_headers()
                self.wfile.write(payload)
                self.wfile.flush()

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
