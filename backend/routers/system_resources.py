"""What this machine is doing and what the app reaches out to.

    GET   /api/system/resources              the snapshot the dashboard's resource indicator shows

No model runs here: speech is Gnani's and the language model and embeddings are Embeddings's, all hosted. So the snapshot is the
machine's memory, this API process's memory, and the hosted services the app uses with whether each has its key. Reading
never calls a hosted service. The whole response is cached for 1 s so that polling is cheap; the macOS compressed-memory
figure (vm_stat) for 2 s.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform as _platform
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional, Tuple

from fastapi import APIRouter, Depends

try:
    from core.errors import AppError  # noqa: F401
    from services.auth import get_current_user_id
except ImportError:  # pragma: no cover - imported as backend.routers.system_resources
    from backend.core.errors import AppError  # noqa: F401
    from backend.services.auth import get_current_user_id

log = logging.getLogger("vakya.system_resources")
router = APIRouter(prefix="/api/system", tags=["system"])

MB = 2 ** 20
SNAPSHOT_TTL_S = 1.0
VM_STAT_TTL_S = 2.0

# kern.memorystatus_vm_pressure_level
PRESSURE_LEVELS = {1: "normal", 2: "warn", 4: "critical"}


def _safe(fn: Callable[[], Any], default: Any = None) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001 - the indicator must never take the page down
        return default


# ------------------------------------------------------------------------------------------------------ the machine

def parse_vm_stat(text: str) -> Optional[float]:
    """Compressed memory in MB from `vm_stat` output (pages occupied by the compressor x page size), or None."""
    import re
    page = re.search(r"page size of (\d+) bytes", text or "")
    pages = re.search(r"Pages occupied by compressor:\s+(\d+)", text or "")
    if not page or not pages:
        return None
    return int(pages.group(1)) * int(page.group(1)) / MB


def pressure_from_level(level: Any) -> Optional[str]:
    try:
        return PRESSURE_LEVELS.get(int(str(level).strip()))
    except (TypeError, ValueError):
        return None


_cache_lock = threading.Lock()
_vm_cache: Tuple[float, Optional[float]] = (0.0, None)
_snapshot_cache: Tuple[float, Optional[dict]] = (0.0, None)


def _run(cmd: List[str]) -> Optional[str]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=2, check=False)
    except Exception:  # noqa: BLE001
        return None
    return out.stdout if out.returncode == 0 else None


def compressed_mb(now: Optional[float] = None) -> Optional[float]:
    """macOS only, cached for 2 s."""
    global _vm_cache
    now = time.monotonic() if now is None else now
    if _platform.system() != "Darwin":
        return None
    at, value = _vm_cache
    if at and now - at < VM_STAT_TTL_S:
        return value
    out = _run(["vm_stat"])
    value = parse_vm_stat(out) if out else None
    _vm_cache = (now, value)
    return value


def memory_pressure() -> Optional[str]:
    if _platform.system() != "Darwin":
        return None
    out = _run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"])
    return pressure_from_level(out) if out else None


def system_block() -> dict:
    import psutil
    vm = psutil.virtual_memory()
    total = vm.total / MB
    available = vm.available / MB
    swap = _safe(psutil.swap_memory)
    system = {"darwin": "darwin", "linux": "linux", "windows": "windows"}.get(_platform.system().lower(), "linux")
    comp = _safe(compressed_mb)
    return {
        "total_mb": round(total),
        "used_mb": round(max(0.0, total - available)),
        "available_mb": round(available),
        "compressed_mb": None if comp is None else round(comp),
        "swap_used_mb": round(swap.used / MB) if swap else 0,
        "swap_total_mb": round(swap.total / MB) if swap else 0,
        "pressure": _safe(memory_pressure),
        "platform": system,
    }


# ------------------------------------------------------------------------------------------------------ hosted services

def services_block() -> List[dict]:
    """The hosted services the app uses and whether each has its key. Nothing is requested."""
    try:
        from services import embeddings, gnani, retrieval
    except ImportError:  # pragma: no cover
        from backend.services import embeddings, gnani, retrieval
    emb = _safe(retrieval.embedder_status, {}) or {}
    return [
        {"id": "gnani", "label": "Gnani AI (Prisma hears, Timbre speaks)", "where": "hosted", "configured": gnani.configured(),
         "reason": None if gnani.configured() else "Needs a Gnani key"},
        {"id": "llm", "label": f"Language model ({embeddings.chat_model()})", "where": "hosted", "configured": embeddings.llm_configured(),
         "reason": None if embeddings.llm_configured() else "Needs a Embeddings key"},
        {"id": "embeddings", "label": f"Knowledge search ({emb.get('model', embeddings.embed_model())})", "where": "hosted",
         "configured": bool(emb.get("configured")), "reason": emb.get("reason"), "mode": emb.get("mode")},
    ]


def app_block() -> dict:
    def own_rss() -> float:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / MB
    return {"api_rss_mb": round(_safe(own_rss, 0.0))}


def build_snapshot() -> dict:
    return {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "system": system_block(),
            "app": app_block(), "services": services_block(), "models": []}


def snapshot(now: Optional[float] = None) -> dict:
    """The response, cached for 1 s."""
    global _snapshot_cache
    now = time.monotonic() if now is None else now
    with _cache_lock:
        at, value = _snapshot_cache
        if value is not None and now - at < SNAPSHOT_TTL_S:
            return value
        value = build_snapshot()
        _snapshot_cache = (now, value)
        return value


def clear_caches() -> None:
    global _vm_cache, _snapshot_cache
    with _cache_lock:
        _vm_cache = (0.0, None)
        _snapshot_cache = (0.0, None)


@router.get("/resources")
async def system_resources(user_id: str = Depends(get_current_user_id)):
    return await asyncio.to_thread(snapshot)
