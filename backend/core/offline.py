"""Offline mode: VAKYA_OFFLINE=1 refuses every network path that could carry audio, text or numbers off this machine.

Blocked: Gnani (speech), Embeddings (language model and embeddings), a language-model server that is not on this machine,
Supabase, Google Calendar and Vobiz phone calls. The desk, the schedules and keyword search over saved documents keep
working. `block()` is the one call every outbound path makes."""
from __future__ import annotations

import ipaddress
import os
from typing import Optional
from urllib.parse import urlsplit

from core.errors import AppError


def enabled() -> bool:
    return os.getenv("VAKYA_OFFLINE", "").strip().lower() in ("1", "true", "on", "yes")


try:  # VAKYA_OFFLINE may be set in backend/.env, which core/state.py only reads later
    from dotenv import load_dotenv
    if not os.getenv("VAKYA_SKIP_DOTENV"):  # the test suite (conftest.py) never reads the real .env
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:  # pragma: no cover
    pass

def is_local_url(url: Optional[str]) -> bool:
    """True when the host of `url` is this machine (localhost, 127.x, ::1) or a private LAN address."""
    try:
        host = urlsplit(url or "").hostname or ""
    except ValueError:
        return False
    if host in ("localhost", "host.docker.internal") or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def reason(what: str) -> str:
    return f"Offline mode is on (VAKYA_OFFLINE=1): {what} is switched off so nothing leaves this computer."


def block(what: str) -> None:
    """Raise a clear 503 when offline mode is on; a no-op otherwise."""
    if enabled():
        raise AppError("OFFLINE_MODE", reason(what), 503, {"blocked": what})
