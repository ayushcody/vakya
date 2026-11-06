"""The server itself: root, health, readiness, the engines on offer and storage health."""

import asyncio
import logging
import os
import shutil
import uuid

from fastapi import APIRouter, Depends

try:
    import httpx
except Exception:  # noqa: BLE001
    httpx = None

try:
    from core import engines, state
    from core.deps import get_current_user
    from core.retry import retry_async
    from services import gnani, licences, llm
except ImportError:  # pragma: no cover
    from backend.core import engines, state
    from backend.core.deps import get_current_user
    from backend.core.retry import retry_async
    from backend.services import gnani, licences, llm

log = logging.getLogger("vakya")

router = APIRouter()


@router.get("/")
async def root():
    return {
        "message": "Vakya - Voice Intelligence API",
        "status": "running",
        "model": engines.MODEL_NAME
    }


@router.get("/api/engines")
async def get_engines():
    """The engines this installation offers, each with its licence (services/licences.py)."""
    return [{"id": k, "name": v["name"], **licences.describe(k), **engines.hosted_engine_flag(k)}
            for k, v in engines.ENGINE_REGISTRY.items()]


@router.get("/health")
async def health_check():
    return {"status": "ok", "engines": list(engines.ENGINE_REGISTRY.keys())}


async def supabase_reachable() -> bool:
    if not state.SUPABASE_URL:
        return False
    if httpx is None:
        return state.supabase is not None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{state.SUPABASE_URL.rstrip('/')}/auth/v1/health",
                                 headers={"apikey": state.SUPABASE_ANON_KEY or ""})
            return r.status_code < 500
    except Exception:  # noqa: BLE001
        return False


def _disk_free_gb() -> float:
    try:
        return round(shutil.disk_usage(state.LOCAL_DATA_DIR).free / (1024 ** 3), 2)
    except OSError:
        return -1.0


def llm_health() -> dict:
    """The language model's part of /api/health: `active` is "embeddings" when a key is set (null otherwise), with the model, a
    provider row (available, and why not: "Needs a Embeddings key") and today's request and token counts."""
    st = llm.status()
    ok = bool(st["configured"])
    return {"active": "embeddings" if ok else None, "model": st["model"] if ok else None,
            "providers": [{"name": "embeddings", "available": ok, "reason": None if ok else (st["reason"] or "Needs a Embeddings key")}],
            "usage_today": llm.usage_today()}


@router.get("/api/ready")
async def api_ready():
    """Is the Gnani side ready, without spending a credit: key present, the gateway (slot, queue, breakers, cache), the usage
    ledger writable, the budget and which provider each lane uses. Also whether the Embeddings key (language model, embeddings) is
    set. Safe to poll (it never calls Gnani or Embeddings)."""
    try:
        from services import gnani_gateway
    except ImportError:  # pragma: no cover
        from backend.services import gnani_gateway
    out = await asyncio.to_thread(gnani_gateway.readiness)
    st = llm.status()
    out["embeddings_configured"] = bool(st["configured"])
    if not st["configured"]:
        out["problems"] = [*out.get("problems", []), "Needs a Embeddings key: set EMBEDDINGS_API_KEY or EMBEDDINGS_API_KEYS (the agent answers with a fixed "
                                                      "line until then)."]
        out["ok"] = False
    return out


@router.get("/api/health")
async def api_health():
    return {
        "status": "ok",
        "heartbeat": heartbeat_service().status(),
        "supabase": {"configured": state.supabase is not None, "reachable": await supabase_reachable()},
        "disk_free_gb": _disk_free_gb(),
        "gnani": {"configured": gnani.configured(), "reason": gnani.engine_unavailable_reason()},
        "llm": llm_health(),
    }


@router.get("/api/storage/health")
async def storage_health():
    result = {"local": {"writable": False, "disk_free_gb": _disk_free_gb()},
              "supabase": {"configured": state.supabase is not None, "reachable": False, "bucket": state.AUDIO_BUCKET,
                           "bucket_ok": False}}
    try:
        probe = os.path.join(state.LOCAL_AUDIO_DIR, f".probe_{uuid.uuid4().hex}")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        result["local"]["writable"] = True
    except OSError:
        pass
    if state.supabase is not None:
        result["supabase"]["reachable"] = await supabase_reachable()
        try:
            await retry_async(lambda: state.supabase.storage.from_(state.AUDIO_BUCKET).list("", {"limit": 1}),
                              label="storage.list")
            result["supabase"]["bucket_ok"] = True
        except Exception as e:  # noqa: BLE001
            result["supabase"]["error"] = str(e)[:200]
    result["status"] = "ok" if (result["local"]["writable"] or result["supabase"]["bucket_ok"]) else "degraded"
    return result


def heartbeat_service():
    try:
        from services.heartbeat import heartbeat as hb
    except ImportError:  # pragma: no cover
        from backend.services.heartbeat import heartbeat as hb
    hb.version = state.APP_VERSION
    return hb
