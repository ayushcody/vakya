"""Ears that learn: the owner corrects what a front desk HEARS (services/hearing.py).

    GET    /api/hearing/{agent_id}                         the desk's corrections and vocabulary, with the limits
    GET    /api/hearing/{agent_id}/rules                   the corrections only ({rules, rule_limit, rules_used})
    POST   /api/hearing/{agent_id}/corrections             teach one: {heard, meant, conversation_id?, turn_index?,
                                                           replace_rule_id?, evict_oldest_unused?}; with a call the rule
                                                           records where it was learnt, without one it is "added by you".
                                                           `meant` also joins the desk's vocabulary (Prisma keyterms)
    DELETE /api/hearing/{agent_id}/rules/{rule_id}         forget a correction
    POST   /api/hearing/{agent_id}/vocabulary              add a word the callers say: {term}
    DELETE /api/hearing/{agent_id}/vocabulary?term=        remove one

A desk keeps at most 10 corrections (Gnani's Prisma takes 10 substitution rules per request): the 11th is a 409 RULES_FULL
that names the oldest unused rule, and succeeds when the request says which rule to replace or asks to replace the oldest
unused one. A word that looks like a phone, card or identity number is a 422 ID_NUMBER. Every route needs the owner of the
front desk (and of the call a correction cites)."""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

try:
    from core import deps
    from core.errors import AppError
    from services import agent_store, hearing
    from services.auth import get_current_user_id
except ImportError:  # pragma: no cover - imported as backend.routers.hearing
    from backend.core import deps
    from backend.core.errors import AppError
    from backend.services import agent_store, hearing
    from backend.services.auth import get_current_user_id

log = logging.getLogger("vakya.hearing")
router = APIRouter(prefix="/api/hearing", tags=["hearing"])


def fail(e: "hearing.HearingError") -> AppError:
    return AppError(e.code, e.message, e.status, {"field": e.field, **({"info": e.details} if e.details else {})})


class CorrectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heard: str = Field(..., max_length=400)
    meant: str = Field(..., max_length=400)
    conversation_id: Optional[str] = Field(None, max_length=80)
    turn_index: Optional[int] = Field(None, ge=0, le=100000)
    replace_rule_id: Optional[str] = Field(None, max_length=20)
    evict_oldest_unused: bool = False


class TermBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term: str = Field(..., max_length=400)


async def _owned(request: Request, agent_id: str, user_id: str) -> dict:
    deps.check_id(agent_id, "agent id")
    return await agent_store.load_agent(request, agent_id, user_id)


async def _check_call(request: Request, user_id: str, agent_id: str, conversation_id: str, turn_index: Optional[int]) -> None:
    """The call a correction cites is the owner's, belongs to this desk, and (with a turn) has that caller line."""
    try:
        from routers import conversations  # noqa: PLC0415 - imported late: it reads the running backend
    except ImportError:  # pragma: no cover
        from backend.routers import conversations  # noqa: PLC0415
    deps.check_id(conversation_id, "conversation id")
    convs, turns, _names = await conversations._load(request, user_id, None, "", conversation_id)   # noqa: SLF001
    conv = next((c for c in convs if c.get("id") == conversation_id), None)
    if conv is None or conv.get("agent_id") != agent_id:
        raise AppError("NOT_FOUND", "That call is not on this front desk.", 404, {"field": "conversation_id"})
    if turn_index is not None:
        transcript = conversations.build_transcript(turns.get(conversation_id, []))
        if turn_index >= len(transcript) or transcript[turn_index]["role"] != "caller":
            raise AppError("INVALID_TURN", "That line is not something the caller said.", 422, {"field": "turn_index"})


@router.get("/{agent_id}")
async def get_hearing(agent_id: str, request: Request, user_id: str = Depends(get_current_user_id)):
    await _owned(request, agent_id, user_id)
    return await asyncio.to_thread(hearing.summary, agent_id)


@router.get("/{agent_id}/rules")
async def get_rules(agent_id: str, request: Request, user_id: str = Depends(get_current_user_id)):
    await _owned(request, agent_id, user_id)
    s = await asyncio.to_thread(hearing.summary, agent_id)
    return {k: s[k] for k in ("agent_id", "rules", "rule_limit", "rules_used", "note")}


@router.post("/{agent_id}/corrections")
async def add_correction(agent_id: str, body: CorrectionBody, request: Request, user_id: str = Depends(get_current_user_id)):
    await _owned(request, agent_id, user_id)
    if body.turn_index is not None and not body.conversation_id:
        raise AppError("INVALID_TURN", "Say which call the line is from.", 422, {"field": "conversation_id"})
    try:
        hearing.check_pair(body.heard, body.meant)        # refuse bad words before looking the call up
    except hearing.HearingError as e:
        raise fail(e) from None
    if body.conversation_id:
        await _check_call(request, user_id, agent_id, body.conversation_id, body.turn_index)
    try:
        out = await asyncio.to_thread(
            lambda: hearing.add_correction(agent_id, body.heard, body.meant, conversation_id=body.conversation_id,
                                           turn=body.turn_index, replace_rule_id=body.replace_rule_id,
                                           evict_oldest_unused=body.evict_oldest_unused))
    except hearing.HearingError as e:
        raise fail(e) from None
    meant = out["rule"]["meant"]
    return {**out, "message": f"From now on Vakya listens for '{meant}' on this desk."}


@router.delete("/{agent_id}/rules/{rule_id}")
async def delete_rule(agent_id: str, rule_id: str, request: Request, user_id: str = Depends(get_current_user_id)):
    await _owned(request, agent_id, user_id)
    try:
        return await asyncio.to_thread(hearing.delete_rule, agent_id, rule_id)
    except hearing.HearingError as e:
        raise fail(e) from None


@router.post("/{agent_id}/vocabulary")
async def add_vocabulary(agent_id: str, body: TermBody, request: Request, user_id: str = Depends(get_current_user_id)):
    await _owned(request, agent_id, user_id)
    try:
        return await asyncio.to_thread(hearing.add_vocabulary, agent_id, body.term)
    except hearing.HearingError as e:
        raise fail(e) from None


@router.delete("/{agent_id}/vocabulary")
async def delete_vocabulary(agent_id: str, request: Request, term: str = Query(..., min_length=1, max_length=400),
                            user_id: str = Depends(get_current_user_id)):
    await _owned(request, agent_id, user_id)
    try:
        return await asyncio.to_thread(hearing.delete_vocabulary, agent_id, term)
    except hearing.HearingError as e:
        raise fail(e) from None
