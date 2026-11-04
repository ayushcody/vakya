"""What Gnani costs, from the usage ledger (services/usage_ledger.py). Every number is an ESTIMATE from the rates in
GET /api/usage/summary -> `rates` (defaults nobody has measured until GNANI_RATE_* are set).

    GET /api/usage/summary?days=7              credits and requests by kind, cache hit rate, p50/p95 latency per kind, queue
                                               wait, degraded requests with reasons and actions, cost per call and per booking, a daily series,
                                               the budget with the reserve marked, and which Gnani model answers each lane and its state
    GET /api/usage/calls?days=30               {conversation_id: {credits, requests, degraded, actions}} for the calls list
    GET /api/usage/calls/{conversation_id}     one call: its requests, credits and degraded requests

Nothing here calls Gnani or spends credits. The ledger is one file for the whole installation; a line written during a
signed-in user's turn carries that user's id and other users do not see it.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query

try:
    from services import gnani_gateway, usage_ledger as ul
    from services.auth import get_current_user_id
except ImportError:  # pragma: no cover - imported as backend.routers.usage
    from backend.services import gnani_gateway, usage_ledger as ul
    from backend.services.auth import get_current_user_id

log = logging.getLogger("vakya.usage")
router = APIRouter(prefix="/api/usage", tags=["usage"])


def _bookings_made(days: int) -> Optional[int]:
    """Bookings made on calls (not cancelled) in the window, the denominator of the cost per booking; None when the store
    cannot be read."""
    try:
        from mcp_servers import appointments  # noqa: PLC0415
        rows = appointments.all_bookings()
    except Exception as e:  # noqa: BLE001 - usage must work without the bookings store
        log.debug("bookings not readable for the usage summary: %s", e)
        return None
    cutoff = datetime.now().astimezone().timestamp() - days * 86400
    count = 0
    for b in rows:
        if b.get("status") == "cancelled" or not b.get("conversation_id"):
            continue
        try:
            made = datetime.fromisoformat(str(b.get("created_at"))).timestamp()
        except (TypeError, ValueError):
            continue
        count += 1 if made >= cutoff else 0
    return count


@router.get("/summary")
async def usage_summary(days: int = Query(7, ge=1, le=365), user_id: str = Depends(get_current_user_id)):
    records = await asyncio.to_thread(ul.LEDGER.records)
    bookings = await asyncio.to_thread(_bookings_made, days)
    out = ul.summarize(records, days=days, bookings=bookings, user=user_id)
    out["providers"] = gnani_gateway.lane_status()
    out["gateway"] = gnani_gateway.state()
    return out


@router.get("/calls")
async def usage_by_call(days: int = Query(30, ge=1, le=365), user_id: str = Depends(get_current_user_id)):
    records = await asyncio.to_thread(ul.LEDGER.records)
    return {"days": days, "estimated": True, "calls": ul.calls_map(records, days=days, user=user_id)}


@router.get("/calls/{conversation_id}")
async def usage_for_call(conversation_id: str, user_id: str = Depends(get_current_user_id)):
    records = await asyncio.to_thread(ul.LEDGER.records)
    return ul.call_usage(records, conversation_id, user=user_id)
