"""Licences of the speech engines, so every receipt names the licence of the engine that spoke.

Vakya speaks only with Gnani's Timbre, a hosted API under Gnani's terms. This is a product safeguard, not legal advice."""
from __future__ import annotations

from typing import Dict, Optional

# engine key -> (licence, cleared for paid use, caveat)
LICENCES: Dict[str, tuple] = {
    # Gnani AI's hosted API (services/gnani.py): the text (and a cloned voice's recording) leave the machine, the terms are
    # Gnani's, and their voice-cloning quotas and consent rules are undocumented, so the consent flow stays in front of it.
    "timbre": ("Gnani AI API terms", True, "hosted API: the text is sent to api.vachana.ai"),
    "timbre_clone": ("Gnani AI API terms", True,
                     "hosted API: the recording goes to api.vachana.ai; Gnani's cloning consent rules are undocumented"),
}


def licence_of(engine: Optional[str]) -> Optional[str]:
    row = LICENCES.get(engine or "")
    return row[0] if row else None


def paid_use_ok(engine: Optional[str]) -> bool:
    """True for engines cleared for paid use; unknown engines count as cleared (they are not voice models we ship)."""
    row = LICENCES.get(engine or "")
    return True if row is None else bool(row[1])


def caveat_of(engine: Optional[str]) -> Optional[str]:
    row = LICENCES.get(engine or "")
    return row[2] if row else None


def describe(engine: str) -> dict:
    """{"licence", "paid_use", "caveat"} for an engine list or a status page."""
    return {"licence": licence_of(engine), "paid_use": paid_use_ok(engine), "caveat": caveat_of(engine)}
