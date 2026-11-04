"""The one door to Gnani AI. Every Prisma, Timbre and voice-clone request in this process goes through `execute()` (the three
public functions of services/gnani_client.py call it), because a Gnani account allows ONE active request at a time and
answers 429 to the second.

There is no second provider: when Gnani is busy or down the gateway does not reach for anything else. It retries inside a
deadline, and then it degrades honestly:

* a LIVE call gets an AppError whose `details` say `action: "hold_line"` (a short wait: play the pre-rendered "one moment
  please" line, services/gnani_hold_lines.py, and try again) or `action: "callback"` (Gnani is down: play the pre-rendered
  "I will call you back" line and offer a callback or a person). The ledger records which.
* desk, preview and benchmark work is QUEUED: it waits for the slot in priority order for a long deadline, is retried with
  backoff, and if the deadline passes the error says `action: "queued"` with `retry_after_s`; nothing is lost, it can be sent
  again.

What the gateway does, in the order a request meets it:

1. cache      the same TTS (text, voice, format, ...) or STT (audio bytes and parameters) is answered from memory: no slot, no
              credits, recorded as a cache hit. A size-capped LRU (GNANI_CACHE_MB, 0 turns it off).
2. breaker    after GNANI_BREAKER_FAILS failed requests in a row (5xx, timeout, unreachable) the breaker opens for
              GNANI_BREAKER_COOLDOWN_S, then lets ONE probe through; a 402 (out of credits) or 401/403 (key rejected)
              opens it for GNANI_ACCOUNT_COOLDOWN_S. An open breaker refuses at once instead of making a caller wait on a
              dead service. The reason is in the error and in the ledger.
3. budget     work that is not a live call may not dip into the credit reserve (services/usage_ledger.guard). Live calls
              are never refused by the budget.
4. slot       one request at a time, process wide, in priority order: live call > desk/preview > bench (first come within a
              lane). A waiter gives up when its deadline is spent.
5. retries    429 and 5xx are retried with exponential backoff and jitter (the slot is released while sleeping, so a live
              request can take it), but never past the lane's deadline: GNANI_LIVE_DEADLINE_S (default 4) for a live call,
              GNANI_QUEUED_DEADLINE_S (default 60) for desk work, GNANI_BENCH_DEADLINE_S (default 600).
6. ledger     one line per request (kind, seconds or characters, estimated credits, latency, queue wait, cache hit, the
              degraded reason and the action taken, conversation id).

The realtime WebSocket holds the slot for its whole session (`hold()`): Gnani counts a stream as the one active request. While
a stream is open, REST requests wait for the slot until their deadline and then degrade as above. Set
GNANI_STREAM_HOLDS_SLOT=off to stream without the slot if a real key shows the limit counts REST requests only.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import copy
import hashlib
import heapq
import itertools
import json
import logging
import os
import random
import struct
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

try:
    from core.errors import AppError
    from services import usage_ledger as ul
except ImportError:  # pragma: no cover - running from a different cwd
    from backend.core.errors import AppError
    from backend.services import usage_ledger as ul

log = logging.getLogger("vakya.gnani")

PRIORITY = {"live": 0, "desk": 1, "bench": 2}
# reasons that usually pass in seconds (a held line is enough) versus those that need a callback or a person
TRANSIENT = frozenset({"busy", "rate_limited", "timeout", "breaker_probe"})

# hooks the tests replace (never sleep or roll dice for real in a test)
_sleep: Callable[[float], None] = time.sleep
_random: Callable[[], float] = random.random
_now: Callable[[], float] = time.monotonic


# ------------------------------------------------------------------------------------------------------ settings

def _num(name: str, default: float, low: float = 0.0) -> float:
    try:
        return max(low, float(os.getenv(name) or default))
    except ValueError:
        return default


def max_wait_s(lane: str) -> float:
    """The longest a request may queue for the slot (the lane's deadline also caps it)."""
    return {"live": _num("GNANI_QUEUE_WAIT_LIVE_S", 3.0), "desk": _num("GNANI_QUEUE_WAIT_DESK_S", 60.0),
            "bench": _num("GNANI_QUEUE_WAIT_BENCH_S", 600.0)}.get(lane, 60.0)


def deadline_s(lane: str) -> float:
    """Wall-clock budget of one request in this lane, queue wait and retries included."""
    return {"live": _num("GNANI_LIVE_DEADLINE_S", 4.0), "desk": _num("GNANI_QUEUED_DEADLINE_S", 60.0),
            "bench": _num("GNANI_BENCH_DEADLINE_S", 600.0)}.get(lane, 60.0)


def max_retries(lane: str = "live") -> int:
    """Live calls retry twice at most (the deadline is short); queued work retries more patiently."""
    return int(_num("GNANI_MAX_RETRIES", 2 if lane == "live" else 5))


def backoff_delay(attempt: int, retry_after: Optional[float] = None) -> float:
    """Exponential backoff with jitter: base * 2^attempt, capped, scaled by a random 0.5..1.0; Retry-After wins when longer."""
    base, cap = _num("GNANI_BACKOFF_BASE_S", 0.4), _num("GNANI_BACKOFF_MAX_S", 4.0)
    delay = min(cap, base * (2 ** attempt)) * (0.5 + 0.5 * _random())
    return max(delay, min(cap, retry_after)) if retry_after else delay


def default_lane() -> str:
    lane = (os.getenv("GNANI_DEFAULT_LANE") or "desk").strip().lower()
    return lane if lane in PRIORITY else "desk"


# ------------------------------------------------------------------------------------------- who is asking (scope)

class Scope:
    """What the requests of one turn (or one desk action) share: the lane and the conversation they belong to."""

    def __init__(self, lane: Optional[str] = None, conversation_id: Optional[str] = None, user_id: Optional[str] = None):
        self.id = uuid.uuid4().hex[:12]
        self.lane = lane if lane in PRIORITY else None
        self.conversation_id = conversation_id
        self.user_id = user_id


_scope: contextvars.ContextVar[Optional[Scope]] = contextvars.ContextVar("vakya_gnani_scope", default=None)


def begin_scope(lane: Optional[str] = None, conversation_id: Optional[str] = None,
                user_id: Optional[str] = None) -> Scope:
    """Start a scope in the current context (the turn pipeline calls this in `turn_begin`: lane "live" for phone and web
    calls, "desk" otherwise). Threads started with asyncio.to_thread inherit it."""
    sc = Scope(lane, conversation_id, user_id)
    _scope.set(sc)
    return sc


def bind_conversation(conversation_id: Optional[str]) -> None:
    """The turn now knows its conversation id: every request of this scope, earlier ones too, belongs to it."""
    sc = _scope.get()
    if sc is None or not conversation_id or sc.conversation_id == conversation_id:
        return
    sc.conversation_id = conversation_id
    ul.record_bind(sc.id, conversation_id)


@contextlib.contextmanager
def scope(lane: Optional[str] = None, conversation_id: Optional[str] = None,
          user_id: Optional[str] = None) -> Iterator[Scope]:
    """`with scope("bench"):` for scripts and tests; restores the previous scope on exit."""
    sc = Scope(lane, conversation_id, user_id)
    token = _scope.set(sc)
    try:
        yield sc
    finally:
        _scope.reset(token)


def current_scope() -> Optional[Scope]:
    return _scope.get()


# ------------------------------------------------------------------------------------------------- the one slot

class SlotBusy(Exception):
    """Raised by the slot when a waiter's time ran out."""


class Slot:
    """One active request at a time. Waiters are served by (lane priority, arrival order)."""

    def __init__(self):
        self._cv = threading.Condition()
        self._busy = False
        self._heap: List[Tuple[int, int, str]] = []
        self._seq = itertools.count()
        self.holder: Optional[Dict[str, Any]] = None

    def acquire(self, lane: str, timeout: float, kind: str = "") -> float:
        """Block until this request is first in line and the slot is free; returns the seconds waited."""
        start = _now()
        ticket = (PRIORITY.get(lane, 1), next(self._seq), lane)
        with self._cv:
            heapq.heappush(self._heap, ticket)
            while True:
                if not self._busy and self._heap[0] == ticket:
                    heapq.heappop(self._heap)
                    self._busy = True
                    self.holder = {"kind": kind, "lane": lane, "since": time.time()}
                    return _now() - start
                remaining = timeout - (_now() - start)
                if remaining <= 0:
                    self._heap.remove(ticket)
                    heapq.heapify(self._heap)
                    self._cv.notify_all()
                    raise SlotBusy(lane)
                self._cv.wait(min(remaining, 0.25))

    def release(self) -> None:
        with self._cv:
            self._busy = False
            self.holder = None
            self._cv.notify_all()

    def state(self) -> Dict[str, Any]:
        with self._cv:
            waiting: Dict[str, int] = {}
            for _, _, lane in self._heap:
                waiting[lane] = waiting.get(lane, 0) + 1
            holder = None
            if self.holder:
                holder = {**self.holder, "held_s": round(time.time() - self.holder["since"], 2)}
                holder.pop("since")
            return {"busy": self._busy, "holder": holder, "waiting": sum(waiting.values()), "waiting_by_lane": waiting}


# ---------------------------------------------------------------------------------------------- circuit breaker

class Breaker:
    """closed -> (N failures in a row) -> open -> (cooldown) -> half_open: one probe -> closed, or open again."""

    def __init__(self, name: str):
        self.name = name
        self.state = "closed"
        self.failures = 0
        self.reason: Optional[str] = None
        self.opened_at = 0.0
        self.cooldown = 0.0
        self.probing = False
        self.lock = threading.Lock()

    def allow(self) -> Tuple[bool, Optional[str]]:
        with self.lock:
            if self.state == "closed":
                return True, None
            if self.state == "open":
                if _now() - self.opened_at < self.cooldown:
                    return False, self.reason
                self.state, self.probing = "half_open", False
            if self.state == "half_open":
                if self.probing:
                    return False, "breaker_probe"
                self.probing = True
                return True, None
            return True, None

    def success(self) -> None:
        with self.lock:
            if self.state != "closed":
                log.info("Gnani %s breaker closed again", self.name)
            self.state, self.failures, self.reason, self.probing = "closed", 0, None, False

    def failure(self, reason: str, *, trip: bool = False, cooldown: Optional[float] = None) -> None:
        with self.lock:
            self.failures += 1
            self.probing = False
            limit = int(_num("GNANI_BREAKER_FAILS", 5, 1))
            if trip or self.state == "half_open" or self.failures >= limit:
                if self.state != "open":
                    log.warning("Gnani %s breaker open: %s (%d failures)", self.name, reason, self.failures)
                self.state, self.reason, self.opened_at = "open", reason, _now()
                self.cooldown = cooldown if cooldown is not None else _num("GNANI_BREAKER_COOLDOWN_S", 30.0)

    def release_probe(self) -> None:
        with self.lock:
            self.probing = False

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            retry = max(0.0, self.cooldown - (_now() - self.opened_at)) if self.state == "open" else 0.0
            return {"state": self.state, "failures": self.failures, "reason": self.reason,
                    "retry_in_s": round(retry, 1)}


# -------------------------------------------------------------------------------------------------------- cache

class Cache:
    """Content-hash LRU capped in bytes. Only deterministic requests use it (the caller supplies the key)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items: "OrderedDict[str, Tuple[Any, int]]" = OrderedDict()
        self.bytes = 0
        self.hits = 0
        self.misses = 0

    @staticmethod
    def cap() -> int:
        return int(_num("GNANI_CACHE_MB", 64.0) * 1024 * 1024)

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            hit = self._items.get(key)
            if hit is None:
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return copy.deepcopy(hit[0])

    def put(self, key: str, value: Any, size: int) -> None:
        cap = self.cap()
        if cap <= 0 or size > cap // 4:
            return
        with self._lock:
            old = self._items.pop(key, None)
            if old:
                self.bytes -= old[1]
            self._items[key] = (copy.deepcopy(value), size)
            self.bytes += size
            while self.bytes > cap and self._items:
                _, (_, sz) = self._items.popitem(last=False)
                self.bytes -= sz

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            looked = self.hits + self.misses
            return {"entries": len(self._items), "bytes": self.bytes, "max_bytes": self.cap(), "hits": self.hits,
                    "misses": self.misses, "hit_rate": round(self.hits / looked, 3) if looked else None}


def cache_key(kind: str, *parts: Any, blobs: Tuple[bytes, ...] = ()) -> str:
    """sha256 over the kind, the JSON of the parameters and the raw bytes (audio, an embedding)."""
    h = hashlib.sha256()
    h.update(kind.encode())
    h.update(json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False).encode())
    for b in blobs:
        h.update(hashlib.sha256(b).digest())
    return h.hexdigest()


# ----------------------------------------------------------------------------------------------------- WAV bytes

def fix_wav_header(data: bytes) -> Tuple[bytes, bool]:
    """Timbre's WAV replies can carry placeholder sizes (0xFFFFFFFF, or 0) in the RIFF and `data` headers, as a streamed
    response does when the length is not known up front. Rewrite them from the real byte count so every reader (and the
    duration estimate) agrees. Returns (bytes, whether anything was changed). Non-WAV bytes come back untouched."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data, False
    out = bytearray(data)
    changed = False
    if struct.unpack_from("<I", out, 4)[0] != len(out) - 8:
        struct.pack_into("<I", out, 4, len(out) - 8)
        changed = True
    pos, block_align = 12, 0
    while pos + 8 <= len(out):
        cid = bytes(out[pos:pos + 4])
        size = struct.unpack_from("<I", out, pos + 4)[0]
        body = pos + 8
        if cid == b"fmt " and body + 14 <= len(out):
            block_align = struct.unpack_from("<H", out, body + 12)[0]
        if cid == b"data":
            avail = len(out) - body
            if size == 0xFFFFFFFF or size > avail or (size == 0 and avail > 0):
                fixed = avail - (avail % block_align) if block_align else avail
                struct.pack_into("<I", out, pos + 4, fixed)
                changed = True
            break
        if size == 0xFFFFFFFF or body + size > len(out):
            break      # a malformed chunk before the data: leave the rest alone
        pos = body + size + (size & 1)
    return (bytes(out), True) if changed else (data, False)


def audio_seconds(data: bytes) -> float:
    """Length of an uploaded clip in seconds: exact for WAV, about len/16,000 for compressed audio (an estimate)."""
    if len(data) >= 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        fixed, _ = fix_wav_header(data)
        pos, byte_rate = 12, 0
        while pos + 8 <= len(fixed):
            cid, size = bytes(fixed[pos:pos + 4]), struct.unpack_from("<I", fixed, pos + 4)[0]
            if cid == b"fmt " and pos + 20 <= len(fixed):
                byte_rate = struct.unpack_from("<I", fixed, pos + 16)[0]
            if cid == b"data":
                return round(size / byte_rate, 3) if byte_rate else 0.0
            pos += 8 + size + (size & 1)
    return round(len(data) / 16000.0, 3)


# ------------------------------------------------------------------------------------------------------ gateway

def _classify(e: AppError) -> Tuple[str, bool, bool]:
    """(reason, counts toward the breaker, retryable) for an error from the client."""
    details = e.details if isinstance(e.details, dict) else {}
    status = details.get("status")
    if e.code == "RATE_LIMITED":
        return "rate_limited", True, True
    if e.code == "PROVIDER_CREDITS":
        return "out_of_credits", True, False
    if e.code == "PROVIDER_AUTH":
        return "auth_rejected", True, False
    if e.code == "PROVIDER_UNAVAILABLE":
        timeout = e.status == 504
        return ("timeout" if timeout else "unreachable"), True, not timeout
    if e.code == "PROVIDER_ERROR" and isinstance(status, int) and status >= 500:
        return "server_error", True, True
    return "request_rejected", False, False     # a 4xx the request itself caused: not Gnani's fault


def plan_for(lane: str, reason: str, retry_in_s: float = 0.0) -> Dict[str, Any]:
    """What the caller should do now that Gnani could not answer. Live: play a held line ("one_moment" for a passing
    problem, "callback" when Gnani is down) and offer a callback or a person. Everything else: it is queued."""
    if lane == "live":
        line = "one_moment" if reason in TRANSIENT else "callback"
        return {"action": "hold_line" if line == "one_moment" else "callback", "hold_line": line}
    if reason == "budget_reserve":
        return {"action": "held_back"}
    return {"action": "queued", "retry_after_s": round(max(retry_in_s, 5.0), 1)}


class Gateway:
    def __init__(self):
        self.slot = Slot()
        self.cache = Cache()
        self.breakers = {k: Breaker(k) for k in ("stt", "tts", "clone")}

    # -- helpers ------------------------------------------------------------------------------------------
    def breaker_for(self, kind: str) -> Breaker:
        return self.breakers["stt" if kind.startswith("stt") else kind]

    def _resolve_lane(self, lane: Optional[str]) -> Tuple[str, Optional[Scope]]:
        sc = _scope.get()
        return (lane if lane in PRIORITY else (sc.lane if sc and sc.lane else default_lane())), sc

    def _log(self, kind: str, lane: str, sc: Optional[Scope], units: Dict[str, Any], **fields: Any) -> None:
        ul.record_request(kind=kind, lane=lane, scope=sc.id if sc else None,
                          conversation_id=sc.conversation_id if sc else None, user=sc.user_id if sc else None,
                          **units, **fields)

    def _degrade(self, kind: str, lane: str, sc: Optional[Scope], units: Dict[str, Any], reason: str, message: str,
                 code: str, status: int, *, retry_in_s: float = 0.0, ok_fields: Optional[Dict[str, Any]] = None,
                 base: Optional[AppError] = None) -> AppError:
        """Record that this request could not be served and build the error the caller sees: the same code the client
        always raised, with `details.degraded` (why) and the plan (`action`, `hold_line`, `retry_after_s`)."""
        plan = plan_for(lane, reason, retry_in_s)
        self._log(kind, lane, sc, units, credits=0.0, ok=False, cache_hit=False, degraded=reason, action=plan["action"],
                  message=message[:200], **(ok_fields or {}))
        if base is not None:
            base.details = {**(base.details if isinstance(base.details, dict) else {}), "degraded": reason, **plan}
            return base
        return AppError(code, message, status, {"provider": "gnani", "degraded": reason, **plan})

    # -- the request --------------------------------------------------------------------------------------
    def execute(self, kind: str, fn: Callable[[], Any], *, units: Dict[str, Any], lane: Optional[str] = None,
                key: Optional[str] = None, size: Callable[[Any], int] = lambda v: len(v) if hasattr(v, "__len__") else 64,
                configured: bool = True) -> Any:
        """Run `fn` (one HTTP call, blocking) under every rule above. `units` is {"seconds": s} or {"chars": n}."""
        if not configured:
            return fn()                       # no key: the client raises its own PROVIDER_NOT_CONFIGURED; nothing to account
        lane, sc = self._resolve_lane(lane)
        estimate = ul.estimate_credits(kind, **units)
        started = _now()
        deadline = started + deadline_s(lane)

        if key and self.cache.cap() > 0:
            hit = self.cache.get(key)
            if hit is not None:
                self._log(kind, lane, sc, units, credits=0.0, latency_ms=0.0, queue_ms=0.0, cache_hit=True, ok=True,
                          attempts=0)
                return hit

        breaker = self.breaker_for(kind)
        ok, why = breaker.allow()
        if not ok:
            snap = breaker.snapshot()
            reason = "breaker_open" if why != "breaker_probe" else "breaker_probe"
            raise self._degrade(kind, lane, sc, units, reason,
                                f"Gnani {kind} is paused ({why or 'recovering'}). Retrying in {snap['retry_in_s']:.0f} s.",
                                "PROVIDER_UNAVAILABLE", 503, retry_in_s=snap["retry_in_s"])

        blocked = ul.guard(lane, estimate)
        if blocked:
            breaker.release_probe()
            raise self._degrade(kind, lane, sc, units, "budget_reserve", blocked, "PROVIDER_CREDITS", 402)

        queued = 0.0
        attempts = 0
        last: Optional[AppError] = None
        for attempt in range(max_retries(lane) + 1):
            attempts += 1
            wait = min(max_wait_s(lane), max(0.0, deadline - _now()))
            try:
                queued += self.slot.acquire(lane, wait, kind)
            except SlotBusy:
                breaker.release_probe()
                if last is not None:
                    raise self._failed(kind, lane, sc, units, last, queued, attempts)
                raise self._degrade(kind, lane, sc, units, "busy",
                                    f"Gnani is busy with another request (it runs one at a time); waited {wait:.0f} s.",
                                    "RATE_LIMITED", 429, retry_in_s=wait)
            t0 = _now()
            try:
                result = fn()
            except AppError as e:
                service = _now() - t0
                self.slot.release()
                reason, counts, retryable = _classify(e)
                last = e
                if retryable and attempt < max_retries(lane):
                    delay = backoff_delay(attempt, (e.details or {}).get("retry_after") if isinstance(e.details, dict) else None)
                    if lane == "live" and e.status == 504:
                        retryable = False          # a live timeout already spent its time
                    if retryable and _now() + delay < deadline:
                        _sleep(delay)
                        continue
                raise self._failed(kind, lane, sc, units, e, queued, attempts, service)
            except BaseException:
                self.slot.release()
                breaker.release_probe()
                raise
            service = _now() - t0
            self.slot.release()
            breaker.success()
            if key:
                self.cache.put(key, result, size(result))
            self._log(kind, lane, sc, units, credits=estimate, latency_ms=round(service * 1000, 1),
                      queue_ms=round(queued * 1000, 1), cache_hit=False, ok=True, attempts=attempts)
            _after_success(kind)
            return result
        raise self._failed(kind, lane, sc, units, last, queued, attempts)   # pragma: no cover - loop always returns/raises

    def _failed(self, kind: str, lane: str, sc: Optional[Scope], units: Dict[str, Any], e: AppError, queued: float,
                attempts: int, service: float = 0.0) -> AppError:
        reason, counts, _ = _classify(e)
        breaker = self.breaker_for(kind)
        if reason in ("out_of_credits", "auth_rejected"):
            cooldown = _num("GNANI_ACCOUNT_COOLDOWN_S", 300.0)
            for b in self.breakers.values():      # an account problem hits every kind
                b.failure(reason, trip=True, cooldown=cooldown)
        elif counts:
            breaker.failure(reason)
        else:
            breaker.release_probe()
        if not counts:       # a request the caller got wrong (4xx): nothing to degrade, just a rejected request
            self._log(kind, lane, sc, units, credits=0.0, latency_ms=round(service * 1000, 1),
                      queue_ms=round(queued * 1000, 1), cache_hit=False, ok=False, attempts=attempts, message=e.message[:200])
            return e
        return self._degrade(kind, lane, sc, units, reason, e.message, e.code, e.status,
                             ok_fields={"latency_ms": round(service * 1000, 1), "queue_ms": round(queued * 1000, 1),
                                        "attempts": attempts}, base=e)

    # -- a held slot (the realtime stream) ---------------------------------------------------------------------
    def hold(self, kind: str = "stt_stream", lane: Optional[str] = None) -> "Hold":
        return Hold(self, kind, lane)

    # -- state ----------------------------------------------------------------------------------------------
    def state(self) -> Dict[str, Any]:
        return {"slot": self.slot.state(), "breakers": {k: b.snapshot() for k, b in self.breakers.items()},
                "cache": self.cache.stats(), "queue_wait_s": {l: max_wait_s(l) for l in PRIORITY},
                "deadline_s": {l: deadline_s(l) for l in PRIORITY}}

    def reset(self) -> None:
        """Forget everything in memory (tests; the ledger file is not touched)."""
        self.__init__()


_after_success_hook: Optional[Callable[[str], None]] = None


def _after_success(kind: str) -> None:
    """Called after every served request; the hold lines module uses it to prepare the lines once Gnani is known to work."""
    if _after_success_hook is not None:
        try:
            _after_success_hook(kind)
        except Exception:  # noqa: BLE001 - never let housekeeping fail a call
            log.debug("after-success hook failed", exc_info=True)


class Hold:
    """A session that keeps the slot: `with gateway.hold(): ...` or `async with ...`. Records one `stt_stream` line when it
    ends, with the audio seconds the caller added through `add_seconds`."""

    def __init__(self, gw: Gateway, kind: str, lane: Optional[str]):
        self.gw, self.kind = gw, kind
        self.lane, self.scope = gw._resolve_lane(lane)
        self.seconds = 0.0
        self.queue_s = 0.0
        self._t0 = 0.0
        self._held = False

    def add_seconds(self, s: float) -> None:
        self.seconds += s

    def _enter(self) -> "Hold":
        if os.getenv("GNANI_STREAM_HOLDS_SLOT", "on").strip().lower() in ("off", "0", "false", "no"):
            self._t0 = _now()
            return self
        breaker = self.gw.breaker_for(self.kind)
        ok, why = breaker.allow()
        units = {"seconds": 0.0}
        if not ok:
            raise self.gw._degrade(self.kind, self.lane, self.scope, units, "breaker_open",
                                   f"Gnani {self.kind} is paused ({why}).", "PROVIDER_UNAVAILABLE", 503,
                                   retry_in_s=breaker.snapshot()["retry_in_s"])
        blocked = ul.guard(self.lane, 0.0)
        if blocked:
            breaker.release_probe()
            raise self.gw._degrade(self.kind, self.lane, self.scope, units, "budget_reserve", blocked,
                                   "PROVIDER_CREDITS", 402)
        wait = min(max_wait_s(self.lane), deadline_s(self.lane))
        try:
            self.queue_s = self.gw.slot.acquire(self.lane, wait, self.kind)
        except SlotBusy:
            breaker.release_probe()
            raise self.gw._degrade(self.kind, self.lane, self.scope, units, "busy",
                                   "Gnani is busy with another request (it runs one at a time).", "RATE_LIMITED", 429,
                                   retry_in_s=wait)
        self._held, self._t0 = True, _now()
        return self

    def _exit(self, ok: bool) -> None:
        if self._held:
            self.gw.slot.release()
            self._held = False
        service = _now() - self._t0
        if ok:
            self.gw.breaker_for(self.kind).success()
        else:
            self.gw.breaker_for(self.kind).release_probe()
        self.gw._log(self.kind, self.lane, self.scope, {"seconds": round(self.seconds, 2)},
                     credits=ul.estimate_credits(self.kind, seconds=self.seconds), latency_ms=round(service * 1000, 1),
                     queue_ms=round(self.queue_s * 1000, 1), cache_hit=False, ok=ok, attempts=1)

    def __enter__(self) -> "Hold":
        return self._enter()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._exit(exc_type is None)
        return False

    async def __aenter__(self) -> "Hold":
        return await asyncio.to_thread(self._enter)

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._exit(exc_type is None)
        return False


GATEWAY = Gateway()


def execute(kind: str, fn: Callable[[], Any], **kw: Any) -> Any:
    return GATEWAY.execute(kind, fn, **kw)


def hold(kind: str = "stt_stream", lane: Optional[str] = None) -> Hold:
    return GATEWAY.hold(kind, lane)


def state() -> Dict[str, Any]:
    return GATEWAY.state()


# ------------------------------------------------------------------------------------------------- what is in use

def lane_status() -> Dict[str, Any]:
    """Which provider answers on each lane right now: always Gnani Prisma (hearing) and Gnani Timbre (speaking), with the state
    (ready, retrying, unavailable, held back, needs a key) and why. Reads the key, the breakers and the budget; sends nothing
    to Gnani and spends no credits."""
    try:
        from services import gnani   # noqa: PLC0415 - gnani imports the client, which imports this module
    except ImportError:  # pragma: no cover
        from backend.services import gnani   # noqa: PLC0415
    configured = gnani.configured()
    st = ul.budget_status()
    out: Dict[str, Any] = {}
    for lane in ("live", "desk"):
        row = {}
        for kind, name in (("stt", "Gnani Prisma"), ("tts", "Gnani Timbre")):
            br = GATEWAY.breaker_for(kind).snapshot()
            base = {"provider": "gnani", "name": name}
            if not configured:
                row[kind] = {**base, "state": "needs_key", "why": "No GNANI_API_KEY is set, so this cannot work yet."}
            elif br["state"] == "open":
                persistent = br["reason"] not in TRANSIENT
                what = ("callers are offered a callback" if lane == "live" else "work is queued") if persistent else "requests are retried"
                row[kind] = {**base, "state": "unavailable" if persistent else "retrying",
                             "why": f"Gnani paused after {br['failures']} failures ({br['reason']}); {what}. Retrying in {br['retry_in_s']:.0f} s."}
            elif lane != "live" and st["reserve_reached"]:
                row[kind] = {**base, "state": "held_back", "why": "Only the credit reserve is left, and it is kept for live calls."}
            else:
                row[kind] = {**base, "state": "ready", "why": "GNANI_API_KEY is set."}
        out[lane] = row
    return out


def readiness() -> Dict[str, Any]:
    """The body of GET /api/ready. Never calls Gnani."""
    try:
        from services import gnani, gnani_hold_lines   # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.services import gnani, gnani_hold_lines   # noqa: PLC0415
    writable, error = ul.LEDGER.check_writable()
    gw = state()
    open_breakers = [k for k, b in gw["breakers"].items() if b["state"] == "open"]
    configured = gnani.configured()
    lines = gnani_hold_lines.status()
    problems = []
    if not configured:
        problems.append("GNANI_API_KEY is not set: Vakya cannot hear or speak until it is.")
    if open_breakers:
        problems.append("Gnani is paused for " + ", ".join(open_breakers) + ": live callers are offered a callback, other work is queued.")
    if not writable:
        problems.append("The usage ledger cannot be written: " + (error or ul.LEDGER.last_error or "unknown error"))
    if configured and not lines["complete"]:
        problems.append("The held lines (\"one moment\", \"I will call you back\") are not prepared yet: " + lines["missing_summary"])
    st = ul.budget_status()
    if st["reserve_reached"]:
        problems.append("Only the credit reserve is left: desk work is held back, live calls continue.")
    return {"ok": configured and writable and not open_breakers, "gnani_configured": configured, "gateway": gw,
            "ledger": {"path": str(ul.ledger_path()), "writable": writable, "error": error or ul.LEDGER.last_error},
            "hold_lines": lines, "budget": st, "providers": lane_status(), "problems": problems, "spends_credits": False}
