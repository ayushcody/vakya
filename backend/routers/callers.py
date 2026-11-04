"""The callers the receptionist remembers (services/caller_profiles.py): one small profile per phone number, kept so a caller who
rings again is greeted by name and not asked for what they already told us.

    GET    /api/callers?limit=50&offset=0&conversation_id=   the owner's caller profiles, newest call first (paginated);
                                                            `conversation_id` narrows it to the caller of that call
    GET    /api/callers/{number}                            one profile; `number` is the phone number (any usual spelling,
                                                            E.164 is best, "%2B" for the plus) or the `id` a listing gave
    PATCH  /api/callers/{number}                            the owner edits what is remembered: display_name,
                                                            preferred_language, facts ({key: value, or null to remove}),
                                                            people ([{name, relation, age_band}], the whole list),
                                                            add_note (one more "ask next time" note) or follow_ups (the
                                                            notes that stay); a value Vakya never keeps (an identity number,
                                                            a card, an OTP, a password, a health condition) is a 422
    PATCH  /api/callers/{number}/people/{name}              edit one person of the family: name (rename), relation (mother,
                                                            father, son, daughter, wife, husband, ..., self, other, in English),
                                                            age_band, facts; returns the whole profile
    DELETE /api/callers/{number}/people/{name}              forget one person of the family, the rest stays
    DELETE /api/callers/{number}                            forget this caller: the profile is deleted at once

Numbers are never returned in full: a profile carries `number_masked` ("••••3210") and an `id` (also `caller_ref`: the
reference the bookings list links to, /desk/callers?caller=<ref>), plus what is remembered: `facts` (labelled chips), `people`
(who is booked for on this number, with the last one in `last_person`), `source` ("phone", "web call" or "owner"). Every route needs the owner
and only ever sees that owner's profiles. With CALLER_PROFILES=off the list is empty and says so (`enabled: false`).
Profiles older than CALLER_PROFILE_RETENTION_DAYS (default 180) are deleted when the backend starts."""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

try:
    from core.errors import AppError
    from services import caller_profiles as cp
    from services.auth import get_current_user_id
except ImportError:  # pragma: no cover - imported as backend.routers.callers
    from backend.core.errors import AppError
    from backend.services import caller_profiles as cp
    from backend.services.auth import get_current_user_id

log = logging.getLogger("vakya.callers")
router = APIRouter(prefix="/api/callers", tags=["callers"])

MAX_KEY = 64


def _b():
    """The running backend (core/runtime.py): every name is read from the module that owns it, at call time."""
    try:
        from core.runtime import runtime  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.core.runtime import runtime  # noqa: PLC0415
    return runtime


def _mode(request: Request) -> str:
    return _b().storage_mode_of(request)


def _store_mode(mode: str) -> str:
    """Hosted mode reads the Supabase table (set up from the backend module)."""
    cp.configure(supabase=lambda: getattr(_b(), "supabase", None))
    return mode


async def _run(mode: str, fn, what: str):
    if mode == "local":
        return await asyncio.to_thread(fn)
    return await _b().cloud_run(fn, what)


def _key(raw: str) -> str:
    key = (raw or "").strip()
    if not key or len(key) > MAX_KEY:
        raise AppError("INVALID_CALLER", "Give the caller's phone number", 422, {"field": "number"})
    return key


@router.get("")
async def list_callers(request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                       conversation_id: Optional[str] = Query(None, max_length=64),
                       user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    mode = _store_mode(_mode(request))
    if not cp.enabled():
        return {"enabled": False, "callers": [], "total": 0, "limit": limit, "offset": offset,
                "retention_days": cp.retention_days()}
    rows = await _run(mode, lambda: cp.list_profiles(user_id, mode), "caller profiles")
    if conversation_id:
        rows = [p for p in rows if any(c.get("conversation_id") == conversation_id for c in p.get("calls") or [])]
    page = rows[offset:offset + limit]
    return {"enabled": True, "callers": [cp.public_view(p) for p in page], "total": len(rows), "limit": limit,
            "offset": offset, "retention_days": cp.retention_days()}


@router.get("/{number}")
async def get_caller(number: str, request: Request, user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    mode = _store_mode(_mode(request))
    key = _key(number)
    profile = await _run(mode, lambda: cp.find(user_id, key, mode), "caller profile") if cp.enabled() else None
    if profile is None:
        raise AppError("NOT_FOUND", "No caller profile for that number", 404)
    return cp.public_view(profile)


class PersonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., max_length=60)
    relation: Optional[str] = Field(None, max_length=24)
    age_band: Optional[str] = Field(None, max_length=12)
    facts: Optional[Dict[str, Optional[str]]] = None
    last_booking: Optional[Dict[str, str]] = None


class PersonEdit(BaseModel):
    """One person of the family, edited from the desk: only the fields sent change."""
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(None, max_length=60)
    relation: Optional[str] = Field(None, max_length=24)
    age_band: Optional[str] = Field(None, max_length=12)
    facts: Optional[Dict[str, Optional[str]]] = None


class CallerEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: Optional[str] = Field(None, max_length=60)
    preferred_language: Optional[str] = Field(None, max_length=8)
    facts: Optional[Dict[str, Optional[str]]] = None
    people: Optional[List[PersonIn]] = Field(None, max_length=cp.PEOPLE_MAX)
    last_person: Optional[str] = Field(None, max_length=60)
    add_note: Optional[str] = Field(None, max_length=cp.NOTE_MAX)
    follow_ups: Optional[List[str]] = Field(None, max_length=cp.MAX_FOLLOW_UPS)


@router.put("/{number}")      # the web app's client has PUT; it means the same as PATCH here (only the fields sent change)
@router.patch("/{number}")
async def edit_caller(number: str, body: CallerEdit, request: Request,
                      user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    mode = _store_mode(_mode(request))
    key = _key(number)
    patch: Dict[str, Any] = body.model_dump(exclude_unset=True)
    if "people" in patch and patch["people"] is not None:
        patch["people"] = [dict(x) for x in patch["people"]]
    patch = {k: v for k, v in patch.items() if v is not None or k in ("display_name", "preferred_language", "last_person")}

    def work():
        try:
            return cp.update_profile(user_id, key, patch, mode)
        except cp.ProfileEditError as e:
            raise AppError("INVALID_CALLER_EDIT", str(e), 422) from None
    profile = await _run(mode, work, "edit caller profile") if cp.enabled() else None
    if profile is None:
        raise AppError("NOT_FOUND", "No caller profile for that number", 404)
    log.info("caller profile edited by its owner")
    return cp.public_view(profile)


@router.put("/{number}/people/{person:path}")      # the web app's client has PUT; same as PATCH
@router.patch("/{number}/people/{person:path}")
async def edit_person(number: str, person: str, body: PersonEdit, request: Request,
                      user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    """Rename a person of the family, change their relation to the caller or age band, or edit their usual details."""
    mode = _store_mode(_mode(request))
    key = _key(number)
    patch: Dict[str, Any] = body.model_dump(exclude_unset=True)
    patch = {k: v for k, v in patch.items() if v is not None or k in ("relation", "age_band")}

    def work():
        try:
            return cp.edit_person(user_id, key, person, patch, mode)
        except cp.ProfileEditError as e:
            raise AppError("INVALID_CALLER_EDIT", str(e), 422) from None
    profile = await _run(mode, work, "edit person") if cp.enabled() else None
    if profile is None:
        raise AppError("NOT_FOUND", "No such person on that number", 404)
    return cp.public_view(profile)


@router.delete("/{number}/people/{person:path}")
async def forget_person(number: str, person: str, request: Request,
                        user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    """Forget one person of the family (their name, relation, details and last booking); the caller and the others stay."""
    mode = _store_mode(_mode(request))
    key = _key(number)
    profile = await _run(mode, lambda: cp.remove_person(user_id, key, person, mode), "forget person") if cp.enabled() else None
    if profile is None:
        raise AppError("NOT_FOUND", "No such person on that number", 404)
    return cp.public_view(profile)


@router.delete("/{number}")
async def forget_caller(number: str, request: Request, user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    mode = _store_mode(_mode(request))
    key = _key(number)
    deleted = await _run(mode, lambda: cp.delete(user_id, key, mode), "delete caller profile")
    if not deleted:
        raise AppError("NOT_FOUND", "No caller profile for that number", 404)
    log.info("caller profile deleted by its owner")
    return {"status": "deleted"}


def install(app) -> None:
    """Called by backend.py when it includes this router: at startup, delete the profiles older than
    CALLER_PROFILE_RETENTION_DAYS (the local file, and the hosted table when Supabase is set up). Never fails the start."""

    async def _prune_at_startup() -> None:
        if not cp.enabled():
            return
        b = _b()
        cp.configure(supabase=lambda: getattr(b, "supabase", None))
        await asyncio.to_thread(cp.prune_expired, "local")
        if getattr(b, "supabase", None) is not None:
            await asyncio.to_thread(cp.prune_expired, "cloud")

    app.on_event("startup")(_prune_at_startup)
