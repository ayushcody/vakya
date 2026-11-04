"""What Gnani requests cost: the rates, the append-only usage ledger, the budget guard and the summaries the API shows.

Every Prisma, Timbre and clone-embedding request that goes through services/gnani_gateway.py leaves one JSON line in the
ledger (VAKYA_USAGE_FILE, default backend/data/usage.jsonl). Nothing is ever rewritten. The numbers are ESTIMATES: credits
are computed from the `rates` below, not read from Gnani's invoice, until someone replaces the defaults with measured
values (docs/GNANI.md says how).

    {"t": "req", "ts", "id", "kind": "stt"|"stt_stream"|"tts"|"clone", "lane": "live"|"desk"|"bench", "seconds"?, "chars"?,
     "credits", "latency_ms" (service time, queue excluded), "queue_ms", "cache_hit", "ok", "attempts",
     "degraded": why Gnani could not answer (busy, breaker_open, server_error, ...) or absent, "action": what the caller was
     told to do (hold_line, callback, queued, held_back; hold_line_played / callback_offered on a "hold_line" line),
     "message"?, "scope", "conversation_id"?, "user"?}
    {"t": "bind", "ts", "scope", "conversation_id"}      a turn learns its conversation id after its first request
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger("vakya.usage")

LANES = ("live", "desk", "bench")
KINDS = ("stt", "stt_stream", "tts", "clone", "hold_line")

# credits per unit. UNVERIFIED defaults taken from a rival entry's measurements (docs/planning/RIVAL_COMPARISON.md);
# replace them with the values this account measures (docs/GNANI.md, "Measuring the rates").
RATE_DEFAULTS: Dict[str, float] = {
    "stt_rest_per_s": 0.0097,        # Prisma REST: credits per second of audio
    "stt_stream_per_s": 0.0073,      # Prisma realtime WebSocket: credits per second of audio
    "tts_per_1k_chars": 2.9,         # Timbre: credits per 1,000 characters
    "clone_per_request": 0.0,        # voice-clone embedding: nobody has measured it; counted as 0 until someone does
}
RATE_ENV = {
    "stt_rest_per_s": "GNANI_RATE_STT_REST_PER_S",
    "stt_stream_per_s": "GNANI_RATE_STT_STREAM_PER_S",
    "tts_per_1k_chars": "GNANI_RATE_TTS_PER_1K_CHARS",
    "clone_per_request": "GNANI_RATE_CLONE_PER_REQUEST",
}
DEFAULT_BUDGET = 5000.0
DEFAULT_RESERVE_PCT = 20.0


def _float_env(name: str, default: Optional[float]) -> Optional[float]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if math.isfinite(value) and value >= 0 else default


def rates() -> Dict[str, Dict[str, Any]]:
    """Each rate with where it came from: "env" (set by the operator, meant to be measured) or "default" (unverified)."""
    out = {}
    for key, default in RATE_DEFAULTS.items():
        env = _float_env(RATE_ENV[key], None)
        out[key] = {"value": default if env is None else env, "source": "default" if env is None else "env",
                    "env": RATE_ENV[key]}
    return out


def rates_verified() -> bool:
    """True only when every rate that matters was set from the environment (the operator's own measurement)."""
    return all(r["source"] == "env" for k, r in rates().items() if k != "clone_per_request")


def estimate_credits(kind: str, *, seconds: Optional[float] = None, chars: Optional[int] = None) -> float:
    r = {k: v["value"] for k, v in rates().items()}
    if kind == "stt":
        return round(float(seconds or 0.0) * r["stt_rest_per_s"], 4)
    if kind == "stt_stream":
        return round(float(seconds or 0.0) * r["stt_stream_per_s"], 4)
    if kind == "tts":
        return round(float(chars or 0) / 1000.0 * r["tts_per_1k_chars"], 4)
    if kind == "clone":
        return round(r["clone_per_request"], 4)
    return 0.0


# ------------------------------------------------------------------------------------------------------- budget

def budget() -> float:
    return _float_env("GNANI_CREDIT_BUDGET", DEFAULT_BUDGET) or 0.0


def reserve_pct() -> float:
    return min(100.0, _float_env("GNANI_CREDIT_RESERVE_PCT", DEFAULT_RESERVE_PCT) or 0.0)


def rupees_per_credit() -> Optional[float]:
    return _float_env("GNANI_RUPEES_PER_CREDIT", None)


def rupees(credits: Optional[float]) -> Optional[float]:
    rate = rupees_per_credit()
    return None if rate is None or credits is None else round(credits * rate, 2)


def budget_status(spent: Optional[float] = None) -> Dict[str, Any]:
    """The budget with the reserve marked: live calls may use everything, other work only what is above the reserve."""
    spent = LEDGER.spent() if spent is None else spent
    total = budget()
    reserve = round(total * reserve_pct() / 100.0, 2)
    usable = round(total - reserve, 2)
    return {"budget": total, "spent": round(spent, 2), "left": round(max(0.0, total - spent), 2),
            "reserve_pct": reserve_pct(), "reserve": reserve, "usable_for_desk": usable,
            "left_for_desk": round(max(0.0, usable - spent), 2), "reserve_reached": spent >= usable,
            "exhausted": spent >= total, "rupees_per_credit": rupees_per_credit(),
            "spent_rupees": rupees(spent), "left_rupees": rupees(max(0.0, total - spent))}


def guard(lane: str, estimate: float) -> Optional[str]:
    """None when the work may go ahead; otherwise one plain sentence saying why not. Live calls are never refused here
    (when Gnani itself refuses one, the gateway tells the caller to play a held line and offer a callback); desk and bench work may not dip into
    the reserve."""
    if lane == "live":
        return None
    st = budget_status()
    if st["spent"] + estimate > st["usable_for_desk"]:
        return (f"Not sent to Gnani: this would use credits held in reserve for live calls "
                f"({st['spent']:.0f} of {st['budget']:.0f} credits used, {st['reserve']:.0f} reserved). "
                f"Raise GNANI_CREDIT_BUDGET or lower GNANI_CREDIT_RESERVE_PCT to allow it.")
    return None


# ------------------------------------------------------------------------------------------------------- ledger

def ledger_path() -> Path:
    env = (os.getenv("VAKYA_USAGE_FILE") or "").strip()
    return Path(env) if env else Path(__file__).resolve().parents[1] / "data" / "usage.jsonl"


class Ledger:
    """Append-only JSONL, one writer per process. The running credit total is kept in memory (read once from the file) so
    the budget guard does not re-read the file on every request."""

    def __init__(self):
        self._lock = threading.RLock()
        self._path: Optional[Path] = None
        self._spent = 0.0
        self.last_error: Optional[str] = None

    def _sync(self) -> None:
        path = ledger_path()
        if path == self._path:
            return
        self._path, self._spent = path, 0.0
        for rec in self._read(path):
            if rec.get("t") == "req":
                self._spent += float(rec.get("credits") or 0.0)

    @staticmethod
    def _read(path: Path) -> Iterable[dict]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue   # a half-written last line is skipped, never repaired in place (append-only)
                    if isinstance(rec, dict):
                        yield rec
        except FileNotFoundError:
            return
        except OSError as e:
            log.warning("usage ledger %s unreadable: %s", path, e)

    def append(self, rec: Dict[str, Any]) -> bool:
        """Write one line. A failure is logged and remembered (GET /api/ready shows it) but never fails a call."""
        rec = {"ts": round(time.time(), 3), **rec}
        with self._lock:
            self._sync()
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            except OSError as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("usage ledger %s not writable: %s", self._path, e)
                return False
            self.last_error = None
            if rec.get("t") == "req":
                self._spent += float(rec.get("credits") or 0.0)
        return True

    def spent(self) -> float:
        with self._lock:
            self._sync()
            return self._spent

    def records(self) -> List[dict]:
        with self._lock:
            self._sync()
            return list(self._read(self._path))

    def check_writable(self) -> Tuple[bool, Optional[str]]:
        """Can a line be appended? Touches nothing but the folder (created if missing); writes no record."""
        path = ledger_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                ok = os.access(path, os.W_OK)
            else:
                ok = os.access(path.parent, os.W_OK)
            return ok, None if ok else f"{path} is not writable"
        except OSError as e:
            return False, f"{type(e).__name__}: {e}"

    def reset_memory(self) -> None:
        with self._lock:
            self._path, self._spent, self.last_error = None, 0.0, None


LEDGER = Ledger()


def record_request(**fields: Any) -> bool:
    return LEDGER.append({"t": "req", **{k: v for k, v in fields.items() if v is not None}})


def record_bind(scope: str, conversation_id: str) -> bool:
    return LEDGER.append({"t": "bind", "scope": scope, "conversation_id": conversation_id})


# -------------------------------------------------------------------------------------------------- summaries

def percentile(values: List[float], pct: float) -> Optional[float]:
    """Linear-interpolated percentile (the same one numpy uses); None for no data."""
    if not values:
        return None
    xs = sorted(values)
    k = (len(xs) - 1) * pct / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)


def _resolve(records: List[dict]) -> List[dict]:
    """The request lines with `conversation_id` filled in from the bind lines of their scope."""
    binds = {r["scope"]: r["conversation_id"] for r in records if r.get("t") == "bind" and r.get("scope")}
    out = []
    for r in records:
        if r.get("t") != "req":
            continue
        conv = r.get("conversation_id") or binds.get(r.get("scope"))
        out.append({**r, "conversation_id": conv} if conv else r)
    return out


def _visible(r: dict, user: Optional[str]) -> bool:
    return not (user and r.get("user") and r["user"] != user)


def _degraded_item(r: dict) -> dict:
    return {"ts": r.get("ts"), "kind": r.get("kind"), "lane": r.get("lane"), "reason": r.get("degraded"),
            "action": r.get("action"), "message": r.get("message"), "conversation_id": r.get("conversation_id")}


def _kind_block(rows: List[dict]) -> Dict[str, Any]:
    served = [r for r in rows if r.get("ok") and not r.get("cache_hit")]
    lat = [float(r["latency_ms"]) for r in served if r.get("latency_ms") is not None]
    queue = [float(r["queue_ms"]) for r in rows if r.get("queue_ms") is not None and not r.get("cache_hit")]
    hits = sum(1 for r in rows if r.get("cache_hit"))
    return {"requests": len(rows), "ok": sum(1 for r in rows if r.get("ok")),
            "credits": round(sum(float(r.get("credits") or 0) for r in rows), 2),
            "seconds": round(sum(float(r.get("seconds") or 0) for r in rows), 1),
            "chars": int(sum(int(r.get("chars") or 0) for r in rows)),
            "cache_hits": hits, "cache_hit_rate": round(hits / len(rows), 3) if rows else None,
            "latency_p50_ms": percentile(lat, 50), "latency_p95_ms": percentile(lat, 95),
            "queue_p50_ms": percentile(queue, 50), "queue_p95_ms": percentile(queue, 95),
            "degraded": sum(1 for r in rows if r.get("degraded"))}


def summarize(records: List[dict], *, days: int = 7, bookings: Optional[int] = None, user: Optional[str] = None,
              now: Optional[float] = None) -> Dict[str, Any]:
    """The usage summary. `bookings` is how many bookings were made on calls in the window (None when unknown): it
    is the denominator of the cost per booking. Bench work is shown but left out of the cost per call and per booking."""
    now = time.time() if now is None else now
    days = max(1, min(int(days), 365))
    since = now - days * 86400
    rows = [r for r in _resolve(records) if _visible(r, user) and float(r.get("ts") or 0) >= since]
    live_rows = [r for r in rows if r.get("lane") != "bench"]
    by_kind = {k: _kind_block([r for r in rows if r.get("kind") == k]) for k in KINDS if any(r.get("kind") == k for r in rows)}
    credits = round(sum(float(r.get("credits") or 0) for r in rows), 2)
    spend = round(sum(float(r.get("credits") or 0) for r in live_rows), 2)
    calls = {r["conversation_id"] for r in live_rows if r.get("conversation_id")}
    fb_rows = [r for r in rows if r.get("degraded") and r.get("kind") != "hold_line"]
    reasons: Dict[str, int] = {}
    actions: Dict[str, int] = {}
    for r in fb_rows:
        reasons[r["degraded"]] = reasons.get(r["degraded"], 0) + 1
        if r.get("action"):
            actions[r["action"]] = actions.get(r["action"], 0) + 1
    played = sum(1 for r in rows if r.get("kind") == "hold_line")
    total_block = _kind_block(rows)
    local_now = datetime.fromtimestamp(now)
    daily = []
    for i in range(days - 1, -1, -1):
        day = (local_now - timedelta(days=i)).date()
        daily.append({"date": day.isoformat(), "credits": 0.0, "requests": 0})
    index = {d["date"]: d for d in daily}
    for r in rows:
        d = index.get(datetime.fromtimestamp(float(r["ts"])).date().isoformat())
        if d:
            d["credits"] = round(d["credits"] + float(r.get("credits") or 0), 4)
            d["requests"] += 1
    verified = rates_verified()
    return {
        "window_days": days, "since": datetime.fromtimestamp(since, timezone.utc).isoformat(),
        "estimate_note": ("Estimated from measured rates." if verified else
                          "Estimated from default rates that nobody has measured on this account yet."),
        "rates_verified": verified, "rates": rates(),
        "totals": {"requests": total_block["requests"], "credits": credits, "rupees": rupees(credits),
                   "cache_hits": total_block["cache_hits"], "cache_hit_rate": total_block["cache_hit_rate"],
                   "degraded": len(fb_rows)},
        "by_kind": by_kind,
        "queue_wait": {"p50_ms": total_block["queue_p50_ms"], "p95_ms": total_block["queue_p95_ms"]},
        "by_lane": {lane: {"requests": sum(1 for r in rows if r.get("lane") == lane),
                           "credits": round(sum(float(r.get("credits") or 0) for r in rows if r.get("lane") == lane), 2)}
                    for lane in LANES},
        "degraded": {"count": len(fb_rows), "by_reason": reasons, "by_action": actions, "hold_lines_played": played,
                     "recent": [_degraded_item(r) for r in fb_rows[-20:]][::-1]},
        "calls": len(calls),
        "cost_per_call": round(spend / len(calls), 2) if calls else None,
        "cost_per_call_rupees": rupees(round(spend / len(calls), 2)) if calls else None,
        "bookings": bookings,
        "cost_per_booking": round(spend / bookings, 2) if bookings else None,
        "cost_per_booking_rupees": rupees(round(spend / bookings, 2)) if bookings else None,
        "daily": daily,
        "budget": budget_status(),
    }


def calls_map(records: List[dict], *, days: int = 30, user: Optional[str] = None,
              now: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """conversation_id -> {credits, requests, degraded, degraded_reasons, actions} for the calls list."""
    now = time.time() if now is None else now
    since = now - max(1, min(int(days), 365)) * 86400
    out: Dict[str, Dict[str, Any]] = {}
    for r in _resolve(records):
        conv = r.get("conversation_id")
        if not conv or not _visible(r, user) or float(r.get("ts") or 0) < since:
            continue
        if r.get("kind") == "hold_line":
            c = out.setdefault(conv, {"credits": 0.0, "requests": 0, "cache_hits": 0, "degraded": 0, "degraded_reasons": [],
                                      "actions": []})
            if r.get("action") and r["action"] not in c["actions"]:
                c["actions"].append(r["action"])
            continue
        c = out.setdefault(conv, {"credits": 0.0, "requests": 0, "cache_hits": 0, "degraded": 0, "degraded_reasons": [],
                                  "actions": []})
        c["credits"] = round(c["credits"] + float(r.get("credits") or 0), 4)
        c["requests"] += 1
        c["cache_hits"] += 1 if r.get("cache_hit") else 0
        if r.get("degraded"):
            c["degraded"] += 1
            if r["degraded"] not in c["degraded_reasons"]:
                c["degraded_reasons"].append(r["degraded"])
            if r.get("action") and r["action"] not in c["actions"]:
                c["actions"].append(r["action"])
    return out


def call_usage(records: List[dict], conversation_id: str, *, user: Optional[str] = None) -> Dict[str, Any]:
    rows = [r for r in _resolve(records) if r.get("conversation_id") == conversation_id and _visible(r, user)]
    credits = round(sum(float(r.get("credits") or 0) for r in rows), 4)
    return {"conversation_id": conversation_id, "requests": len(rows), "credits": credits, "rupees": rupees(credits),
            "estimated": True, "by_kind": {k: _kind_block([r for r in rows if r.get("kind") == k]) for k in KINDS
                                           if any(r.get("kind") == k for r in rows)},
            "degraded": [_degraded_item(r) for r in rows if r.get("degraded")],
            "items": [{k: r.get(k) for k in ("ts", "kind", "lane", "seconds", "chars", "credits", "latency_ms",
                                              "queue_ms", "cache_hit", "ok", "degraded", "action")} for r in rows[-100:]]}
