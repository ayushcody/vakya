"""Itemised consent record for a cloned voice, notice version and retention.

When a voice is cloned Vakya stores one record that states what the person agreed to: the purpose, what is kept,
the sentence they read and the code in it, how well the recording matched, when, how long
the voice is kept and which version of the plain-language notice they were shown. The record is shown to the voice
owner (GET /api/voices/{id}/consent-record) and can be downloaded as JSON.

Retention: a voice is kept for VOICE_RETENTION_DAYS (default 365) from the day it was cloned. Listing the voices
past that date is report-only. Voices are deleted automatically only when VOICE_RETENTION_ENFORCE=on.

This is a record-keeping aid, not legal advice. Whether it meets a given law (India's DPDP Rules 2025, the IT
Rules on synthetically generated information, GDPR ...) is for the operator and their lawyer to decide."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

# Bump when the wording of the notice shown in the clone wizard changes (frontend/src/components/voices/noticeText.ts keeps
# the same constant; tests/test_compliance.py checks that they match).
NOTICE_VERSION = "2026-11-03"

DEFAULT_RETENTION_DAYS = 365
RECORD_VERSION = 1

# What is stored for every cloned voice (shown in the record and the notice).
DATA_STORED = ("voice sample", "consent recording", "consent transcript (the sentence you read)",
               "voice embedding (a numeric fingerprint of the sample)")

# One-tick consent (CONSENT_MODE=tick, the default): the person ticks one box instead of reading a sentence aloud.
# What the box says is stored in the record and (in cloud mode) on the consents row. Bump NOTICE_VERSION if it changes.
TICK_STATEMENT = "I own this voice or have the speaker's permission to clone it for Vakya"
DATA_STORED_TICK = ("voice sample", "voice embedding (a numeric fingerprint of the sample)",
                    "consent tick (when, who ticked, and a hash of the sample)")
CONSENT_MODES = ("tick", "strict")
_TRUE = ("true", "1", "on", "yes", "y")

_CODE_RE = re.compile(r"\bmy code is\s+(.+?)\s*\.?\s*$", re.IGNORECASE)


def consent_mode() -> str:
    """CONSENT_MODE: "tick" (default; one checkbox) or "strict" (read a one-time sentence aloud, checked against
    its transcript). Anything else gives the default. Read on every call, so a restart is not needed in tests."""
    value = os.getenv("CONSENT_MODE", "").strip().lower()
    return value if value in CONSENT_MODES else "tick"


def is_ticked(value: Any) -> bool:
    """True for a form value that means the box was ticked ("true", "1", "on", "yes")."""
    return str(value if value is not None else "").strip().lower() in _TRUE


def sample_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tick_verification() -> Dict[str, Any]:
    """What clone_voice_core records for a ticked consent: nothing was heard or compared, the person said yes."""
    return {"verified": True, "score": None, "heard": None, "method": "tick"}


def operator_name() -> str:
    """Who runs this Vakya studio (named in the purpose sentence). Set VAKYA_OPERATOR_NAME."""
    return os.getenv("VAKYA_OPERATOR_NAME", "").strip() or "the owner of this Vakya studio"


def contact() -> str:
    """Where a person can send a question or a withdrawal request. Set VAKYA_PRIVACY_CONTACT."""
    return os.getenv("VAKYA_PRIVACY_CONTACT", "").strip() or "the person who runs this Vakya studio"


def retention_days() -> int:
    """VOICE_RETENTION_DAYS (default 365). A missing, non-numeric or non-positive value gives the default."""
    try:
        days = int(os.getenv("VOICE_RETENTION_DAYS", "").strip() or DEFAULT_RETENTION_DAYS)
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    return days if days > 0 else DEFAULT_RETENTION_DAYS


def enforce_enabled() -> bool:
    """True unless VOICE_RETENTION_ENFORCE is off: expired voices are deleted at startup and on request. On by default
    since 22 Oct 2026, because the consent record states a retention period and report-only did not keep it."""
    return os.getenv("VOICE_RETENTION_ENFORCE", "on").strip().lower() not in ("off", "0", "false", "no")


def _parse(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.astimezone()  # a naive timestamp is local time (voices.json stores datetime.now())


def retain_until(created_at: Optional[str], days: Optional[int] = None) -> Optional[str]:
    """ISO date-time (UTC) when a voice made at `created_at` is due for deletion, or None when the date is unknown."""
    start = _parse(created_at)
    if start is None:
        return None
    return (start + timedelta(days=days if days is not None else retention_days())).astimezone(
        timezone.utc).isoformat(timespec="seconds")


def is_expired(created_at: Optional[str], now: Optional[datetime] = None, days: Optional[int] = None) -> bool:
    end = _parse(retain_until(created_at, days))
    if end is None:
        return False  # unknown age: never treated as expired
    return (now or datetime.now(timezone.utc)) >= end


def expired_voices(voices: Iterable[dict], now: Optional[datetime] = None) -> List[dict]:
    """Voices past their retention date, as {"id", "name", "created_at", "retain_until"} (report-only, deletes nothing).

    A voice's own record may carry a different retention period (set when it was cloned); that one wins."""
    out = []
    for v in voices:
        rec = v.get("consent_record") if isinstance(v.get("consent_record"), dict) else {}
        days = rec.get("retention_days") if isinstance(rec.get("retention_days"), int) and rec.get("retention_days") > 0 \
            else retention_days()
        created = rec.get("consented_at") or v.get("created_at")
        if is_expired(created, now, days):
            out.append({"id": v.get("id"), "name": v.get("display_name") or v.get("name"),
                        "created_at": created, "retain_until": retain_until(created, days)})
    return out


def challenge_code(sentence: Optional[str]) -> Optional[str]:
    """The random code read out in a challenge sentence ("... My code is mango 42."), or None."""
    m = _CODE_RE.search((sentence or "").strip())
    return m.group(1).strip() if m else None


def build_record(*, voice_id: str, voice_name: str, consent_id: Optional[str], challenge_sentence: Optional[str],
                 challenge_id: Optional[str], verification: Dict[str, Any], language: str, engine: str,
                 created_at: Optional[str] = None, method: str = "challenge", consented_by: Optional[str] = None,
                 sample_sha256: Optional[str] = None) -> dict:
    """The record stored when a voice is cloned. `verification` is what verify_consent returned.

    `method` is "challenge" (a sentence was read aloud and checked) or "tick" (one box was ticked: no sentence, no
    transcript or speaker score; `consented_by` is the name given with the tick, else the operator, and
    `sample_sha256` ties the tick to the exact sample)."""
    now = datetime.now(timezone.utc)
    days = retention_days()
    created = created_at or now.isoformat(timespec="seconds")
    return {
        "record_version": RECORD_VERSION,
        "notice_version": NOTICE_VERSION,
        "voice_id": voice_id,
        "voice_name": voice_name,
        "consent_id": consent_id,
        "purpose": f"Create an AI version of this voice for use in Vakya by {operator_name()}.",
        "data_stored": list(DATA_STORED_TICK if method == "tick" else DATA_STORED),
        "method": method,
        "consented_by": (consented_by or "").strip() or (operator_name() if method == "tick" else None),
        "consent_statement": TICK_STATEMENT if method == "tick" else None,
        "sample_sha256": sample_sha256,
        "challenge_sentence": challenge_sentence or None,
        "challenge_code": challenge_code(challenge_sentence),
        "challenge_id": challenge_id or None,
        "transcript_match_score": verification.get("score"),
        "transcript_verified": bool(verification.get("verified")),
        "language": language,
        "engine": engine,
        "consented_at": created,
        "retention_days": days,
        "retain_until": retain_until(created, days),
        "how_to_withdraw": "Choose \"Withdraw consent and delete\" on the voice in the Voices page. That deletes the "
                           "sample, the embedding and the consent recording. Clips already made stay, marked as "
                           "made with a voice whose owner withdrew consent.",
        "contact": contact(),
        "legacy": False,
    }


def record_for_row(row: dict) -> dict:
    """The consent record of a stored voice row. Voices cloned before records existed get a partial one built from
    the row (marked legacy), with the fields that were never stored left as None."""
    rec = row.get("consent_record")
    if isinstance(rec, dict) and rec.get("voice_id"):
        out = dict(rec)
        out.setdefault("legacy", False)
        # the retention date follows the current policy for display only when the record has none
        if not out.get("retain_until"):
            out["retain_until"] = retain_until(out.get("consented_at"), out.get("retention_days"))
        return out
    created = row.get("created_at")
    verified = row.get("consent_verified")
    days = retention_days()
    return {
        "record_version": 0,
        "notice_version": None,
        "voice_id": row.get("id"),
        "voice_name": row.get("display_name") or row.get("name"),
        "consent_id": row.get("consent_id") or None,
        "purpose": f"Create an AI version of this voice for use in Vakya by {operator_name()}.",
        "data_stored": list(DATA_STORED),
        "method": None,
        "consented_by": None,
        "consent_statement": None,
        "sample_sha256": None,
        "challenge_sentence": None,
        "challenge_code": None,
        "challenge_id": row.get("consent_challenge_id") or None,
        "transcript_match_score": None,
        "transcript_verified": bool(verified) if verified is not None else None,
        "language": row.get("language"),
        "engine": row.get("engine"),
        "consented_at": created,
        "retention_days": days,
        "retain_until": retain_until(created, days),
        "how_to_withdraw": "Choose \"Withdraw consent and delete\" on the voice in the Voices page.",
        "contact": contact(),
        "legacy": True,
    }
