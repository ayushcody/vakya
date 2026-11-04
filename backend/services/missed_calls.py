"""Missed-call callbacks: the log of what happened to each missed call, and the rules that decide whether to call back.

A caller rings the clinic's Vobiz missed-call number, whose application's Answer URL (routers/phone.py, /api/phone/missed-call)
rejects the call before it is answered. The receptionist then rings the caller back a few seconds later, so the caller never has to wait on hold or
call twice ("Call once, it's booked").

This module holds no network code:
  * enabled()         MISSED_CALL_CALLBACK=on|off (default on)
  * delay_s()         MISSED_CALL_DELAY_S (default 8): how long after the hang-up the callback is placed
  * dedupe_s()        MISSED_CALL_DEDUPE_S (default 600): at most one callback per number in that time
  * claim(number)     the dedupe gate: True once per number per window (the first caller wins, also under concurrency)
  * record(...)       one event row in data/missed_calls.jsonl (VAKYA_MISSED_CALLS_FILE), numbers masked only
  * list_for(owner)   the owner's events, newest first
An event has `hit` (one id per missed call, shared by its events), `status` received | skipped | called_back | failed, and a
`reason` for skipped and failed ones.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

_lock = threading.RLock()
STATUSES = ("received", "skipped", "called_back", "failed")
KEEP_ROWS = 1000
_last_callback: Dict[str, float] = {}        # E.164 number -> when its callback was last claimed (memory only)

REASONS = {
    "disabled": "Missed-call callbacks are switched off (MISSED_CALL_CALLBACK=off).",
    "bad_number": "The caller's number was missing or not a valid number.",
    "not_allowed": "The number is not in PHONE_ALLOWED_NUMBERS (a trial account can only call allowed numbers).",
    "recent_callback": "This number was already called back in the last few minutes.",
    "call_live": "A call with this number is happening right now.",
    "rate_limited": "Too many calls this hour.",
    "not_configured": "The phone line is not set up yet (see Settings).",
    "no_agent": "No receptionist was found to make the call.",
}


def _flag(name: str, default: str) -> str:
    return os.getenv(name, default).strip().lower()


def enabled() -> bool:
    return _flag("MISSED_CALL_CALLBACK", "on") not in ("off", "0", "false", "no")


def _seconds(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default)).strip()))
    except ValueError:
        return default


def delay_s() -> float:
    return _seconds("MISSED_CALL_DELAY_S", 8.0)


def dedupe_s() -> float:
    return _seconds("MISSED_CALL_DEDUPE_S", 600.0)


def claim(number: str, now: Optional[float] = None) -> bool:
    """True when `number` has had no callback in the last dedupe_s() seconds; the claim is made in the same step, so two
    hits at once cannot both pass."""
    t = time.time() if now is None else now
    with _lock:
        last = _last_callback.get(number)
        if last is not None and t - last < dedupe_s():
            return False
        _last_callback[number] = t
        for k in [k for k, v in _last_callback.items() if t - v >= dedupe_s()]:   # forget the old ones
            if k != number:
                del _last_callback[k]
        return True


def release(number: str) -> None:
    """Give a claim back (the callback was dropped before a call was placed, for a reason that is not the number's fault)."""
    with _lock:
        _last_callback.pop(number, None)


def reset() -> None:
    with _lock:
        _last_callback.clear()


# ------------------------------------------------------------------------------------------------ the event log

def _path() -> str:
    env = os.getenv("VAKYA_MISSED_CALLS_FILE", "").strip()
    return env or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "missed_calls.jsonl")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_hit() -> str:
    return uuid.uuid4().hex[:12]


def record(hit: str, status: str, owner: Optional[str] = None, reason: Optional[str] = None, **fields: Any) -> Dict[str, Any]:
    """Append one event. `fields` may carry number_masked, call_to_masked, agent_id, agent_name, call_sid (Vobiz's CallUUID of the
    missed call), callback_sid (the id of the call placed), conversation_id and message. Never raises: a log that cannot be
    written must not stop a callback."""
    row: Dict[str, Any] = {"id": uuid.uuid4().hex[:12], "hit": hit, "at": _now_iso(), "status": status if status in STATUSES else "failed",
                           "reason": reason, "owner": owner}
    for k in ("number_masked", "call_to_masked", "agent_id", "agent_name", "call_sid", "callback_sid", "conversation_id", "message"):
        if fields.get(k) is not None:
            row[k] = fields[k]
    try:
        with _lock:
            path = _path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            _trim(path)
    except Exception:  # noqa: BLE001
        pass
    return row


def _trim(path: str) -> None:
    try:
        if os.path.getsize(path) < 400_000:
            return
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > KEEP_ROWS:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(lines[-KEEP_ROWS:])
            os.replace(tmp, path)
    except OSError:
        pass


def list_for(owner: str, limit: int = 50) -> List[Dict[str, Any]]:
    """The owner's events, newest first."""
    rows: List[Dict[str, Any]] = []
    with _lock:
        try:
            with open(_path(), "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("owner") == owner:
                        rows.append(row)
        except OSError:
            pass
    rows.reverse()
    return rows[:max(1, min(limit, 200))]
