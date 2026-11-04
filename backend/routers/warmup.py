"""Get ready for a live demo: check the keys, open the connections and prepare the held lines.

Nothing loads on this machine: speech is Gnani's (Prisma hears, Timbre speaks) and the language model and the embeddings are
Gemini's. So warming is small. POST /api/warmup runs, in order: the Gnani key (a check, no request), the language model
(one tiny request, which also opens the connection), knowledge search (one embedding), and the held lines (the short
"One moment please" and "I will call you back" clips, rendered once by Timbre and kept on disk: a few requests the first
time, none after). GET /api/warmup reports progress.

`warm_live_turn_path()` (also `warm_for_live_turns()`) is the light, automatic version: only what a live reply needs, in
parallel, each step timed and logged, never raising. It runs in the background when the backend starts (WARM_ON_START=
auto|on|off, see `warm_on_start`) and may be called again whenever a call is about to start (a phone ringing). The filler
and disclosure clips are rendered ahead only with WARM_CLIPS=on (they cost a few Timbre requests at every start; without
it each is rendered the first time a call needs it and kept for the process).

The backend module is imported lazily inside the workers so this router never creates an import cycle."""

try:
    from core import state
except ImportError:  # pragma: no cover
    from backend.core import state

import asyncio
import logging
import os
import threading
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

try:
    from services import gnani, gnani_hold_lines, llm
    from services.auth import get_current_user_id
    from core.errors import AppError
except ImportError:  # imported as backend.routers.warmup
    from backend.services import gnani, gnani_hold_lines, llm
    from backend.services.auth import get_current_user_id
    from backend.core.errors import AppError

log = logging.getLogger("vakya.warmup")

router = APIRouter(prefix="/api/warmup", tags=["warmup"])


class Skipped(Exception):
    """A component that cannot run now (carries the reason)."""


def _b():
    """The running backend (core/runtime.py): every name is read from the module that owns it, at call time."""
    try:
        from core.runtime import runtime  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.core.runtime import runtime  # noqa: PLC0415
    return runtime


def vobiz_configured() -> bool:
    """The three Vobiz settings of the phone channel are set (see routers/phone.py). The public URL is not needed to answer
    a call."""
    return all((os.getenv(name) or "").strip() for name in ("VOBIZ_AUTH_ID", "VOBIZ_AUTH_TOKEN", "VOBIZ_CALLER_ID"))


def clips_warm_enabled() -> bool:
    return (os.getenv("WARM_CLIPS", "off") or "off").strip().lower() in ("on", "1", "true", "yes")


# ------------------------------------------------------------------------------------------------ components
# Each runner returns a short detail string, raises Skipped(reason) when the component does not apply now, and raises
# anything else when it fails. They run on the event loop and push blocking work into threads.

async def _warm_gnani() -> str:
    """The Gnani key is set (nothing is requested: a request would spend credits)."""
    reason = gnani.engine_unavailable_reason()
    if reason:
        raise Skipped(reason)
    return f"Prisma {gnani.stt_model()} and Timbre {gnani.tts_model()}: key set"


async def _warm_llm() -> str:
    st = llm.status()
    if not st["configured"]:
        raise Skipped(st["reason"] or "Needs a Gemini key")

    def work() -> str:
        try:
            res = llm.chat([{"role": "user", "content": "Reply with the single word OK."}],
                           temperature=0.0, max_tokens=4, timeout=60.0)
        except llm.LLMUnavailable as e:
            raise Skipped("; ".join(f"{k}: {v}" for k, v in e.reasons.items()) or "no language model available") from e
        return f"{res.provider} · {res.model}"

    return await asyncio.to_thread(work)


async def _warm_embedding() -> str:
    b = _b()
    st = b.retrieval.embedder_status()
    if not st["configured"]:
        raise Skipped(f"{st['reason'] or 'Needs a Gemini key'}: search by keyword only")

    def work() -> str:
        # one document-sized text and one query, so the first real search does not pay for the connection
        vectors = b.embedding_model.encode(["What is the refund window?", "रिफंड कितने दिन में मिलता है"])
        return f"{st['model']} · {len(vectors[0])}-dimensional vectors"

    return await asyncio.to_thread(work)


async def _warm_hold_lines() -> str:
    """Render the held lines that are not on disk yet (spends a few Timbre requests the first time, none after)."""
    if not gnani.configured():
        raise Skipped(gnani.engine_unavailable_reason() or "Needs a Gnani key")
    before = gnani_hold_lines.status()
    if before["complete"]:
        return f"{before['present']} of {before['expected']} held lines ready"
    out = await asyncio.to_thread(gnani_hold_lines.prepare)
    if out.get("failed"):
        raise AppError("PROVIDER_ERROR", f"{len(out['failed'])} held line(s) could not be rendered: {out['failed'][0]}", 502)
    return f"{out['made']} held line(s) rendered, {out['present']} of {out['expected']} ready"


async def _warm_clips() -> str:
    if not gnani.configured():
        raise Skipped(gnani.engine_unavailable_reason() or "Needs a Gnani key")
    count = await _b().prerender_turn_clips()
    return f"{count} clips rendered"


# id, label, runner name (looked up at run time)
_COMPONENTS = [
    ("gnani", "Speech (Gnani Prisma and Timbre)", "_warm_gnani"),
    ("llm", "Language model (Gemini)", "_warm_llm"),
    ("embedding", "Knowledge search (Gemini embeddings)", "_warm_embedding"),
    ("hold_lines", "Held lines (\"one moment\", \"I will call you back\")", "_warm_hold_lines"),
]


# ------------------------------------------------------------------------------------------------ state

_lock = threading.Lock()
_state: Dict[str, Any] = {"state": "idle", "started_at": None, "finished_at": None, "components": []}
_task: Optional["asyncio.Task"] = None


def _fresh_components() -> List[dict]:
    return [{"id": cid, "label": label, "state": "pending", "seconds": None, "detail": None}
            for cid, label, _fn in _COMPONENTS]


def _snapshot() -> dict:
    with _lock:
        comps = _state["components"] or _fresh_components()
        return {"state": _state["state"], "started_at": _state["started_at"], "finished_at": _state["finished_at"],
                "components": [dict(c) for c in comps]}


def _finish_state(components: List[dict]) -> str:
    return "partial" if any(c["state"] == "failed" for c in components) else "ready"


async def _run() -> None:
    with _lock:
        components = _state["components"]
    runners: Dict[str, Callable[[], Awaitable[str]]] = {cid: globals()[fn] for cid, _l, fn in _COMPONENTS}
    try:
        for comp in components:
            with _lock:
                comp["state"] = "warming"
            started = time.time()
            state_, detail = "ready", None
            try:
                detail = await runners[comp["id"]]()
            except Skipped as e:
                state_, detail = "skipped", str(e)
            except AppError as e:
                state_, detail = "failed", f"{e.code}: {e.message}"[:300]
                log.warning("warm-up of %s failed: %s", comp["id"], detail)
            except Exception as e:  # noqa: BLE001 - one bad component must not stop the others
                state_, detail = "failed", f"{type(e).__name__}: {e}"[:300]
                log.warning("warm-up of %s failed: %s", comp["id"], detail, exc_info=True)
            with _lock:
                comp.update(state=state_, detail=detail, seconds=round(time.time() - started, 2))
    finally:
        with _lock:
            for comp in components:  # only reachable on cancellation: do not leave anything "warming"
                if comp["state"] in ("warming", "pending"):
                    comp["state"] = "failed"
                    comp["detail"] = comp["detail"] or "interrupted"
            _state["state"] = _finish_state(components)
            _state["finished_at"] = time.time()
        log.info("warm-up finished: %s", _state["state"])


@router.post("")
async def start_warmup(include: str = Query("", max_length=64), user_id: str = Depends(get_current_user_id)):
    """Start warming in the background; when a warm-up is already running, return its current state instead."""
    global _task
    with _lock:
        if _state["state"] != "running":
            _state.update(state="running", started_at=time.time(), finished_at=None, components=_fresh_components())
            _task = asyncio.create_task(_run())
    return _snapshot()


@router.get("")
async def get_warmup(user_id: str = Depends(get_current_user_id)):
    return _snapshot()


# ------------------------------------------------------------------------------------------------ live-turn warm-up

STEP_IDS = ("gnani", "llm", "hold_lines", "clips", "embedding", "knowledge")
KNOWLEDGE_WARM_AGENTS = 5   # agents whose knowledge search is run once (their chunks are read, checked and tokenized)
_live_task: Optional["asyncio.Future"] = None


async def _step(results: Dict[str, dict], name: str, fn: Callable[[], Awaitable[str]]) -> bool:
    """Run one warm-up step: time it, log it, record it in `results`, never raise. True when it worked."""
    started = time.time()
    state_, detail = "ready", None
    try:
        detail = await fn()
    except Skipped as e:
        state_, detail = "skipped", str(e)
    except asyncio.CancelledError:
        raise
    except AppError as e:
        state_, detail = "failed", f"{e.code}: {e.message}"[:200]
    except Exception as e:  # noqa: BLE001 - one step failing must not stop the others
        state_, detail = "failed", f"{type(e).__name__}: {e}"[:200]
    seconds = round(time.time() - started, 2)
    results[name] = {"state": state_, "seconds": seconds, "detail": detail}
    (log.warning if state_ == "failed" else log.info)("warm-up step %s: %s in %.2f s%s", name, state_, seconds,
                                                       f" ({detail})" if detail else "")
    return state_ == "ready"


async def _warm_llm_connection() -> str:
    if not llm.configured():
        raise Skipped(llm.status()["reason"] or "Needs a Gemini key")
    if not llm._prewarm_targets():
        raise Skipped("no hosted language model is configured")
    await asyncio.to_thread(llm.prewarm, wait=True)
    return "connection to the hosted model opened"


def _warm_agent_extras(agent: dict) -> None:
    """Start what the agent's first turn would otherwise lack: its fact sheet (Key facts in the prompt) and its
    speech-recognition vocabulary and name list. Both build in the background; a failure only costs the extra."""
    try:
        from services import fact_sheet, query_understanding  # noqa: PLC0415
        fact_sheet.prefetch(agent["id"], agent.get("user_id"), "local")
        query_understanding.get_profile(agent, agent.get("user_id"), "local")
    except Exception as e:  # noqa: BLE001
        log.debug("agent warm-up extras skipped: %s", e)


async def _warm_knowledge(agent_id: Optional[str]) -> str:
    """Run the knowledge search once for the agents a live call may reach, so its first real search finds the
    keyword index built and the table checks done (see backend.refresh_local_embeddings)."""
    b = _b()
    agents = await asyncio.to_thread(b.local_db.select, "agents")
    if agent_id:
        agents = [a for a in agents if a.get("id") == agent_id]
    done = 0
    for agent in agents[:KNOWLEDGE_WARM_AGENTS]:
        await b.retrieve_context(agent["id"], "opening hours", user_id=agent.get("user_id"), storage_mode="local")
        _warm_agent_extras(agent)
        done += 1
    if not done:
        raise Skipped("no agents yet")
    return f"{done} agent(s)"


async def _live_warm(reason: str, agent_id: Optional[str]) -> Dict[str, dict]:
    results: Dict[str, dict] = {}
    started = time.time()

    async def voice_chain() -> None:
        await _step(results, "gnani", _warm_gnani)
        ok = await _step(results, "hold_lines", _warm_hold_lines)
        if ok and clips_warm_enabled():
            await _step(results, "clips", _warm_clips)
        else:
            results["clips"] = {"state": "skipped", "seconds": 0.0,
                                "detail": "WARM_CLIPS is not on" if ok else "no voice to render them with"}

    async def knowledge_chain() -> None:
        await _step(results, "embedding", _warm_embedding)
        await _step(results, "knowledge", lambda: _warm_knowledge(agent_id))   # keyword search needs no embedder

    await asyncio.gather(
        _step(results, "llm", _warm_llm_connection),
        voice_chain(),
        knowledge_chain(),
    )
    ordered = {k: results[k] for k in STEP_IDS if k in results}
    log.info("warm-up for live turns (%s) finished in %.2f s: %s", reason, time.time() - started,
             ", ".join(f"{k} {v['state']} {v['seconds']:.2f}s" for k, v in ordered.items()))
    return ordered


async def warm_live_turn_path(reason: str = "manual", agent_id: Optional[str] = None) -> Dict[str, dict]:
    """Get a live spoken reply ready, now: the Gnani key check and the held lines, the language model's connection
    (llm.prewarm), the embedder with one search and, with WARM_CLIPS=on, the filler and disclosure clips. Steps run in
    parallel and are timed and logged; a failing step is reported, never raised. A call made while a warm-up is already
    running joins it. Returns {step: {"state": ready|skipped|failed, "seconds", "detail"}}.

    `agent_id` limits the knowledge step to one agent (a phone about to ring for it)."""
    global _live_task
    task = _live_task
    if task is None or task.done():
        task = _live_task = asyncio.ensure_future(_live_warm(reason, agent_id))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - belt and braces: a warm-up must never raise into its caller
        log.warning("warm-up for live turns failed: %s", e)
        return {}


# ---- the start-up warm-up ---------------------------------------------------------------------------------------
# The backend prepares the held lines and opens the connections in the background when it starts, so the first call
# does not wait for them (and never delays startup or the health checks: the work is a task that the startup hook does
# not await).

_BACKGROUND_TASKS: set = set()


def warm_on_start() -> bool:
    """WARM_ON_START=auto|on|off (default auto): auto warms when a phone line (Vobiz) is configured or any agent
    exists. Never under pytest unless WARM_ON_START=on (the tests must not call out)."""
    mode = (os.getenv("WARM_ON_START", "auto") or "auto").strip().lower()
    if mode in ("off", "0", "false", "no"):
        return False
    if mode in ("on", "1", "true", "yes"):
        return True
    if "PYTEST_CURRENT_TEST" in os.environ:
        return False
    if vobiz_configured():
        return True
    try:
        return bool(state.local_db.select("agents", limit=1))
    except Exception:  # noqa: BLE001
        return False


async def warm_for_live_turns(reason: str = "manual", agent_id: Optional[str] = None) -> Dict[str, dict]:
    """Get everything a live spoken reply needs ready (the hook for the phone agent: `await
    warm_for_live_turns("ringing", agent_id)`): the Gnani key and held lines, the language model's connection, the
    embedder with one search. It skips what is ready, so calling it again is cheap. Never raises; returns each step's
    {"state", "seconds", "detail"}."""
    return await warm_live_turn_path(reason, agent_id)


def start_background_warmup() -> None:
    """Called by the startup hook: begin the warm-up if WARM_ON_START allows it, without waiting for it."""
    try:
        if not warm_on_start():
            log.info("startup warm-up is off (WARM_ON_START=%s)", os.getenv("WARM_ON_START", "auto"))
            return
        task = asyncio.get_running_loop().create_task(warm_for_live_turns("startup"))
        _BACKGROUND_TASKS.add(task)  # a task nobody holds can be garbage collected half way
        task.add_done_callback(_BACKGROUND_TASKS.discard)
    except Exception as e:  # noqa: BLE001 - starting must never fail because of the warm-up
        log.warning("startup warm-up not started: %s", e)
