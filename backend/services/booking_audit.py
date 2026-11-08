"""The booking audit log: an append-only JSONL file (backend/data/booking_audit.jsonl, VAKYA_BOOKING_AUDIT_FILE) with one line for
every create, move, cancel and refusal, whoever did it.

    {"ts", "event" "create" | "move" | "cancel" | "refusal", "actor" "agent" | "staff" | "api", "conversation_id", "booking_id",
     "schedule_id", "phone" (••••3210: the last four digits only), "before", "after" (the booking's time and status, null when
     there was none), "reason" (a machine code, e.g. TOO_SOON, SLOT_FULL, CANCEL_TOO_LATE), "attempted" (a refusal: what was
     asked for), "owner" (the signed-in owner, when the route knows it)}

Lines are only ever appended (one write each, under a lock shared by threads and processes) and never rewritten. Nothing here
raises: a failing disk must never undo or block a booking. A caller's own number is never stored whole."""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

log = logging.getLogger("vakya.booking_audit")
_thread_lock = threading.Lock()
_call: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("vakya_audit_call", default=None)
EVENTS = ("create", "move", "cancel", "refusal")


def audit_path() -> str:
    return os.getenv("VAKYA_BOOKING_AUDIT_FILE") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "booking_audit.jsonl")


def bind_call(conversation_id: Any) -> Optional[contextvars.Token]:
    """Tie the bookings made in this context (a phone or web call's tool) to its conversation. Reset the token after."""
    return _call.set(str(conversation_id)) if conversation_id else None


def reset_call(token: Optional[contextvars.Token]) -> None:
    if token is not None:
        _call.reset(token)


def current_call() -> Optional[str]:
    return _call.get()


def mask_phone(value: Any) -> Optional[str]:
    digits = re.sub(r"\D", "", str(value or ""))
    return (f"••••{digits[-4:]}" if len(digits) >= 5 else "••••") if digits else None


def snapshot(row: Optional[dict]) -> Optional[dict]:
    """The part of a booking the log keeps: when it is, how many nights, its status and version."""
    if not row:
        return None
    out = {k: row.get(k) for k in ("date", "time", "start", "end", "nights", "status", "version") if row.get(k) is not None}
    return out or None


def record(event: str, *, actor: str, booking_id: Optional[str] = None, schedule_id: Optional[str] = None, phone: Any = None,
           before: Optional[dict] = None, after: Optional[dict] = None, reason: Optional[str] = None,
           attempted: Optional[dict] = None, conversation_id: Optional[str] = None, owner: Optional[str] = None,
           now: Optional[datetime] = None) -> Optional[dict]:
    """Append one line. Returns the entry (None when it could not be written). Never raises."""
    try:
        entry = {"ts": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "event": event, "actor": actor,
                 "conversation_id": conversation_id or current_call(), "booking_id": booking_id, "schedule_id": schedule_id,
                 "phone": mask_phone(phone), "before": snapshot(before), "after": snapshot(after), "reason": reason}
        if attempted:
            entry["attempted"] = attempted
        if owner:
            entry["owner"] = owner
        line = (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        path = audit_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _thread_lock:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                if fcntl:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                os.write(fd, line)
            finally:
                os.close(fd)   # releases the flock
        return entry
    except Exception as e:  # noqa: BLE001
        log.warning("booking audit line not written: %r", e)
        return None


def read(limit: int = 100, owner: Optional[str] = None, booking_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """The newest `limit` entries, newest first. With `owner`, entries of other owners are left out (entries with no owner,
    such as the agent's, are kept: the bookings list is the same file for every signed-in owner)."""
    limit = max(1, min(int(limit), 1000))
    try:
        with open(audit_path(), encoding="utf-8", errors="replace") as f:     # a torn multi-byte write must not hide the log
            lines = f.read().splitlines()
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or (owner and e.get("owner") not in (None, owner)):
            continue
        if booking_id and e.get("booking_id") != booking_id:
            continue
        out.append({k: v for k, v in e.items() if k != "owner"})
        if len(out) >= limit:
            break
    return out
