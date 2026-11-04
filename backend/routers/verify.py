"""Public provenance checks: upload a clip to see whether Vakya made it, and fetch a signed receipt.

No sign-in is needed: anyone (a journalist, a platform, a listener) can check a clip. The result is evidence, not
proof: an exact file match against a signed receipt holds only for an unmodified copy.

The backend module is imported lazily inside handlers so this router never creates an import cycle."""
import asyncio
import logging
import re

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse

try:
    from services import booking_evidence, provenance
    from core.errors import AppError
    from core.validation import read_limited, sniff_audio_kind
except ImportError:  # imported as backend.routers.verify
    from backend.services import booking_evidence, provenance
    from backend.core.errors import AppError
    from backend.core.validation import read_limited, sniff_audio_kind

log = logging.getLogger("vakya.verify")

router = APIRouter(tags=["verify"])

MAX_VERIFY_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = (".wav", ".mp3", ".m4a", ".webm", ".flac", ".ogg")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _b():
    """The running backend (core/runtime.py): every name is read from the module that owns it, at call time."""
    try:
        from core.runtime import runtime  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.core.runtime import runtime  # noqa: PLC0415
    return runtime


def _limit(rule: str):
    """The app's rate limiter (same buckets as every other endpoint); a no-op if it cannot be found."""
    try:
        return _b().limiter.limit(rule)
    except Exception:  # noqa: BLE001
        return lambda fn: fn


@router.post("/api/verify")
@_limit("20/minute")
async def verify_clip(request: Request, file: UploadFile = File(...)):
    """Check an uploaded clip (wav, mp3, m4a, webm; at most 20 MB).

    Returns {"manifest": {...} | null, "match": {exact, receipt, signature_valid},
    "verdict": "made_with_vakya" | "not_detected"}."""
    name = (file.filename or "").lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise AppError("INVALID_FILE_TYPE", f"Unsupported file type '{ext or 'unknown'}'. Upload a wav, mp3, m4a "
                       "or webm clip.", 415, {"allowed": list(ALLOWED_EXTENSIONS)})
    data = await read_limited(file, MAX_VERIFY_BYTES)
    if not data:
        raise AppError("EMPTY_FILE", "Uploaded file is empty", 400)
    if sniff_audio_kind(data) is None:
        raise AppError("INVALID_FILE_CONTENT", "File content does not look like a supported audio file", 415)
    return await asyncio.to_thread(provenance.verify_clip, data)


@router.get("/api/receipts/{receipt_id}")
@_limit("60/minute")
async def get_receipt(request: Request, receipt_id: str):
    """A receipt as anyone may see it (no owner), with whether its signature checks out."""
    if not ID_RE.match(receipt_id):
        raise AppError("INVALID_ID", "Invalid receipt id", 422, {"field": "receipt_id"})
    receipt = await asyncio.to_thread(provenance.get_receipt, receipt_id)
    if receipt is None:
        raise AppError("NOT_FOUND", "Receipt not found", 404)
    return provenance.public_receipt(receipt)


@router.get("/api/receipts/{receipt_id}/booking")
@_limit("60/minute")
async def get_booking_evidence(request: Request, receipt_id: str):
    """For a receipt of kind "booking": how the booking came about, as a timeline (asked, said, read back, confirmed) with
    the hash of each step and whether everything still matches the signed receipt (`verified`). Without a sign-in the
    transcripts are redacted (the caller's name and number are hidden, and a spoken name or number is replaced by a
    placeholder) and no audio is offered, unless the owner made the proof public (`public`). It never names the caller."""
    if not ID_RE.match(receipt_id):
        raise AppError("INVALID_ID", "Invalid receipt id", 422, {"field": "receipt_id"})
    view = await asyncio.to_thread(booking_evidence.public_view, receipt_id)
    if view is None:
        raise AppError("NOT_FOUND", "No booking proof for this receipt", 404)
    return view


@router.get("/api/receipts/{receipt_id}/booking/audio/{item_id}")
@_limit("30/minute")
async def get_booking_evidence_audio(request: Request, receipt_id: str, item_id: str):
    """One step's recording, only when the owner made the proof public; otherwise 404."""
    if not ID_RE.match(receipt_id) or not re.fullmatch(r"i\d{1,3}", item_id):
        raise AppError("INVALID_ID", "Invalid id", 422)
    path = await asyncio.to_thread(booking_evidence.audio_path, receipt_id, item_id, public=True)
    if not path:
        raise AppError("NOT_FOUND", "No public recording for this step", 404)
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.get("/.well-known/vakya-receipt-key")
@_limit("60/minute")
async def receipt_key(request: Request):
    """The public keys that check Ed25519 receipt signatures and the signed withdrawal log, so a third party can verify
    a receipt offline: Ed25519 over the canonical JSON of `signed_fields` (sorted keys, no spaces, UTF-8). `keys` holds
    the current key first and every retired one after it; a verifier picks the entry whose `key_id` the receipt names
    (a receipt without `key_id` predates rotation: try each)."""
    public = await asyncio.to_thread(provenance.public_key_b64)
    if public is None:
        raise AppError("NOT_AVAILABLE", "This server does not sign receipts with Ed25519", 404)
    kid = provenance.key_id(public)
    return {"alg": "Ed25519", "key_id": kid, "public_key": public,
            "signed_fields": list(provenance.SIGNED_FIELDS), "receipt_version": 1,
            "keys": await asyncio.to_thread(provenance.all_public_keys)}


@router.get("/api/withdrawals")
@_limit("30/minute")
async def withdrawal_log(request: Request):
    """The public withdrawal log: which voices had their consent withdrawn (or were deleted at the end of retention),
    when, hash-chained and signed. It holds ids and dates only. `check` says whether the chain verifies."""
    rows = await asyncio.to_thread(provenance.withdrawal_rows)
    check = await asyncio.to_thread(provenance.verify_withdrawal_log, rows)
    return {"rows": rows, "check": check, "key_id": provenance.key_id()}
