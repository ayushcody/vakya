"""A small client for Meta's WhatsApp Cloud API: it sends one approved template message to one number.

Business-initiated messages (a confirmation sent after the call is over) must be approved *templates*; free text is only
allowed inside a 24-hour window after the customer wrote to the business. So this module only sends templates.

Settings (backend/.env):
    WHATSAPP_ACCESS_TOKEN       a permanent token from a system user of the Business app (never shown, never logged)
    WHATSAPP_PHONE_NUMBER_ID    the id of the sending number (not the number itself)
    WHATSAPP_API_VERSION        Graph API version, default v27.0
    WHATSAPP_TEMPLATE_CONFIRMED / _MOVED / _CANCELLED   template names, default vakya_booking_confirmed / _moved / _cancelled

Request shape used (positional body parameters, {{1}}, {{2}} ... in the template text):
    POST https://graph.facebook.com/<version>/<phone-number-id>/messages        Authorization: Bearer <token>
    {"messaging_product": "whatsapp", "recipient_type": "individual", "to": "91XXXXXXXXXX", "type": "template",
     "template": {"name": "...", "language": {"code": "en"},
                  "components": [{"type": "body", "parameters": [{"type": "text", "text": "..."}]}]}}
    -> {"messages": [{"id": "wamid...."}]}        an error is {"error": {"message", "type", "code", "fbtrace_id"}}

Docs used: the endpoint, version v27.0 and the response id are in
https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages.md ; the error shape in
.../direct-send/send-utility-and-authentication-messages.md ; positional vs named placeholders when a template is created in
.../templates/components.md . The template send payload above follows Meta's template-message guide; the pages fetched while
writing this did not print a full template example, so the first real send is the check (see docs/WHATSAPP.md).

`_http` is the one place a request leaves the program; tests replace it."""
from __future__ import annotations

import logging
import os
import re
from typing import Callable, List, Optional, Tuple

try:
    from core import offline
except ImportError:  # pragma: no cover
    from backend.core import offline

log = logging.getLogger("vakya.whatsapp")

DEFAULT_VERSION = "v27.0"
TIMEOUT_S = 10.0
KINDS = ("booked", "moved", "cancelled")
DEFAULT_TEMPLATES = {"booked": "vakya_booking_confirmed", "moved": "vakya_booking_moved",
                     "cancelled": "vakya_booking_cancelled"}
_ENV_TEMPLATE = {"booked": "WHATSAPP_TEMPLATE_CONFIRMED", "moved": "WHATSAPP_TEMPLATE_MOVED",
                 "cancelled": "WHATSAPP_TEMPLATE_CANCELLED"}


class WhatsAppError(RuntimeError):
    """Meta refused the message or could not be reached. `code` is Meta's error code when it gave one."""

    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


def _token() -> str:
    return os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()


def phone_number_id() -> str:
    return os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()


def api_version() -> str:
    return os.getenv("WHATSAPP_API_VERSION", "").strip() or DEFAULT_VERSION


def template_name(kind: str) -> str:
    return os.getenv(_ENV_TEMPLATE[kind], "").strip() or DEFAULT_TEMPLATES[kind]


def template_names() -> dict:
    return {k: template_name(k) for k in KINDS}


def configured() -> bool:
    if offline.enabled():
        return False
    return bool(_token()) and bool(phone_number_id())


def missing() -> List[str]:
    return [n for n, v in (("WHATSAPP_ACCESS_TOKEN", _token()), ("WHATSAPP_PHONE_NUMBER_ID", phone_number_id())) if not v]


def status() -> dict:
    """What the settings page may say: never the token."""
    return {"configured": configured(), "access_token_set": bool(_token()),
            "phone_number_id_set": bool(phone_number_id()), "missing": missing(),
            "api_version": api_version(), "templates": template_names()}


def to_e164(number: str) -> str:
    """91XXXXXXXXXX (digits only, the form the Cloud API takes) for a 10-digit Indian number or a +91 / 91 / 0 prefixed one."""
    digits = re.sub(r"\D", "", str(number or ""))
    if len(digits) == 10:
        return "91" + digits
    if len(digits) == 11 and digits.startswith("0"):
        return "91" + digits[1:]
    if len(digits) == 12 and digits.startswith("91"):
        return digits
    raise WhatsAppError("That is not an Indian mobile number.")


def _http(method: str, url: str, *, headers: Optional[dict] = None, json_body: Optional[dict] = None) -> Tuple[int, dict]:
    import httpx  # noqa: PLC0415
    try:
        r = httpx.request(method, url, headers=headers, json=json_body, timeout=TIMEOUT_S)
    except httpx.HTTPError as e:
        raise WhatsAppError(f"Could not reach WhatsApp ({e.__class__.__name__}).") from e
    try:
        body = r.json() if r.content else {}
    except ValueError:
        body = {"raw": r.text[:200]}
    return r.status_code, body


def _scrub(text: str) -> str:
    token = _token()
    return text.replace(token, "[token]") if token else text


def send_template(to: str, template: str, language_code: str, params: List[str]) -> str:
    """Send `template` in `language_code` with the body `params`; returns Meta's message id. Raises WhatsAppError."""
    if not configured():
        raise WhatsAppError("WhatsApp is not set up (WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID).")
    url = f"https://graph.facebook.com/{api_version()}/{phone_number_id()}/messages"
    body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to_e164(to), "type": "template",
            "template": {"name": template, "language": {"code": language_code},
                         "components": [{"type": "body", "parameters": [{"type": "text", "text": str(p)} for p in params]}]}}
    code, data = _http("POST", url, headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
                       json_body=body)
    if code >= 300 or (isinstance(data, dict) and data.get("error")):
        err = (data or {}).get("error") or {}
        msg = _scrub(str(err.get("message") or f"HTTP {code}"))
        log.warning("whatsapp send refused: %s (code %s)", msg, err.get("code"))
        raise WhatsAppError(f"WhatsApp refused the message: {msg}", err.get("code") if isinstance(err.get("code"), int) else None)
    try:
        return str(data["messages"][0]["id"])
    except (KeyError, IndexError, TypeError) as e:
        raise WhatsAppError("WhatsApp answered without a message id.") from e
