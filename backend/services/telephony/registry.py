"""Which provider carries the calls, and whether it is set up.

Vakya's only carrier is Vobiz. PHONE_PROVIDERS (default `vobiz`) is still read so an existing backend/.env keeps working: it
may only name `vobiz`, and anything else is ignored with one warning. The one Vobiz account comes from VOBIZ_AUTH_ID,
VOBIZ_AUTH_TOKEN and VOBIZ_CALLER_ID (optionally VOBIZ_BASE_URL)."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .base import Provider, env
from .numbers import mask_caller_id

log = logging.getLogger("vakya.phone")

NAMES = ("vobiz",)
DEFAULT_PROVIDERS = ("vobiz",)
_warned: set = set()


def provider(name: str = "vobiz") -> Provider:
    """The provider with this name. KeyError for an unknown name."""
    if name.lower() not in NAMES:
        raise KeyError(name)
    from . import vobiz as mod  # noqa: PLC0415
    return mod.PROVIDER


def enabled() -> List[str]:
    """PHONE_PROVIDERS as a list of known provider names (default: vobiz)."""
    out: List[str] = []
    for raw in env("PHONE_PROVIDERS", "vobiz").split(","):
        name = raw.strip().lower()
        if not name:
            continue
        if name not in NAMES:
            if name not in _warned:
                _warned.add(name)
                log.warning("PHONE_PROVIDERS names %r, which Vakya no longer supports (Vobiz only); it is ignored", name)
            continue
        if name not in out:
            out.append(name)
    return out or list(DEFAULT_PROVIDERS)


def account() -> Optional[Dict[str, str]]:
    """The Vobiz account from backend/.env, or None when a setting is missing."""
    return provider().account()


def missing_settings() -> List[str]:
    """[] when the Vobiz account is complete and PUBLIC_BASE_URL is set; otherwise the names still to be set."""
    miss = provider().missing_vars()
    if not env("PUBLIC_BASE_URL"):
        miss.append("PUBLIC_BASE_URL")
    return miss


def public_status() -> List[dict]:
    """For GET /api/phone/status: the provider, whether it is set up, and its masked caller id. Never a key or token."""
    p = provider()
    a = p.account()
    return [{"name": p.name, "display": p.display, "configured": a is not None, "enabled": True, "verified": p.verified,
             "verification_note": p.verification_note, "caller_id_masked": mask_caller_id(a["caller_id"]) if a else None}]
