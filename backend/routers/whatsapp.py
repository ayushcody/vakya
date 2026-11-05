"""WhatsApp confirmations for the signed-in owner (services/booking_notify.py, services/whatsapp.py).

    GET /api/whatsapp/status     whether the Cloud API is set up (never the token), what is missing, the three template
                                 names, the send delays, and counts of messages by state
    GET /api/whatsapp/messages   the last 50 messages: masked number (••••3210), kind, booking id, the text, state
                                 ("sent" went to Meta, "preview" was built but NOT sent, "failed", "skipped") and time
"""
from fastapi import APIRouter, Depends, Query

try:
    from services import booking_notify, whatsapp
    from services.auth import get_current_user_id
except ImportError:  # pragma: no cover - imported as backend.routers.whatsapp
    from backend.services import booking_notify, whatsapp
    from backend.services.auth import get_current_user_id

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])


@router.get("/status")
async def whatsapp_status(user_id: str = Depends(get_current_user_id)):
    return {**whatsapp.status(), "confirmations_on": booking_notify.enabled(), "preview_on": booking_notify.preview_on(),
            "send_delay_s": booking_notify.send_delay_s(), "backstop_s": booking_notify.backstop_s(),
            "counts": booking_notify.counts(user_id)}


@router.get("/messages")
async def whatsapp_messages(limit: int = Query(50, ge=1, le=50), user_id: str = Depends(get_current_user_id)):
    return {"messages": booking_notify.recent_messages(user_id, limit)}
