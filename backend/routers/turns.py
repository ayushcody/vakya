"""The spoken conversation turn: the blocking and the streaming endpoint, and the audio of a turn and of its sentences."""

import asyncio
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

try:
    from core import deps, state
    from core.deps import get_current_user
    from core.errors import AppError
    from core.validation import validate_audio_upload
    from services import call_events, turn_pipeline, turn_stream
    from services.turn_pipeline import TurnRun
except ImportError:  # pragma: no cover
    from backend.core import deps, state
    from backend.core.deps import get_current_user
    from backend.core.errors import AppError
    from backend.core.validation import validate_audio_upload
    from backend.services import call_events, turn_pipeline, turn_stream
    from backend.services.turn_pipeline import TurnRun


router = APIRouter()


@router.post("/api/conversations/{agent_id}/turn")
async def process_turn(
    agent_id: str,
    request: Request,
    audio: UploadFile = File(None),
    text: str = Form(None),
    conversation_id: str = Form(None),
    test_call: str = Form(None),
    user_id: str = Depends(get_current_user)
):
    """One turn: a recording (`audio`), or a typed question (`text`, the Talk page's text box; no speech recognition)."""
    typed = (text or "").strip()
    if audio is None and not typed:
        raise AppError("NO_INPUT", "Send a recording or type a question.", 422)
    if len(typed) > turn_pipeline.TYPED_TURN_MAX_CHARS:
        raise AppError("TEXT_TOO_LONG", f"Keep a typed question under {turn_pipeline.TYPED_TURN_MAX_CHARS} characters.", 422)
    run = await turn_pipeline.turn_begin(request, agent_id, user_id)
    run.is_test = turn_pipeline.form_flag(test_call)
    if typed and audio is None:
        run.text_input = typed
        return await turn_pipeline.turn_blocking(run, b"", conversation_id)
    file_bytes = await validate_audio_upload(audio, allow_recording=True, field="audio")
    return await turn_pipeline.turn_blocking(run, file_bytes, conversation_id)


@router.post("/api/conversations/{agent_id}/turn/stream")
async def process_turn_stream(
    agent_id: str,
    request: Request,
    audio: UploadFile = File(None),
    text: str = Form(None),
    conversation_id: str = Form(None),
    test_call: str = Form(None),
    user_id: str = Depends(get_current_user)
):
    """Same request as the turn endpoint (`test_call=true` marks a new conversation as the owner's simulated Test
    call: it is kept out of Analytics); the answer is a `text/event-stream`:

    - `transcript` {user_text, language}: right after speech recognition
    - `sentence` {index, text, audio_url}: each sentence of the reply as soon as its audio is ready (the first one is
      the AI disclosure when the agent says it); audio_url is an owner-checked GET (a data: URI in cloud mode)
      A slow answer (nothing ready FILLER_AFTER_MS after the transcript, default 900; PHONE_FILLER_AFTER_MS, default
      500, on a phone call) is preceded by one short filler sentence ("One moment.") with `filler: true`; it is not
      part of agent_text or the audio in `done`, and is never sent while the AI disclosure plays, nor once the model
      has started answering. FILLER_AUDIO=off turns it off. The first sentence of a reply is cut short at its first
      clause (TURN_FIRST_CLAUSE, see services/sentences.py), so the first `sentence` may be half a sentence.
    - `done` {turn_id, agent_text, agent_audio_url (the whole reply), timings {asr_ms, retrieval_ms,
      llm_first_token_ms, llm_ms, first_audio_ms, tts_ms, total_ms}, engine, llm_provider, llm_model, language,
      fallback, receipt_id, conversation_id, ...}. A turn that asks a clarifying question (speech recognition was
      unsure; services/canned_reply.py: no retrieval, tools or language model) also has `canned_reply` {id,
      audio_cached}, timings.canned_reply_id, llm_provider null and retrieval_ms = llm_ms = 0.
    - `error` {code, message}: the stream ends after it

    `text` instead of `audio` is a typed message (no speech recognition; the reply is still spoken). The turn also
    writes its events to the call event log (services/call_events.py, docs/ARCHITECTURE.md "Live call events").

    Problems found before any work starts (unknown agent, bad audio) are ordinary HTTP errors."""
    typed = (text or "").strip()
    if audio is None and not typed:
        raise AppError("NO_INPUT", "Send a recording or type a message.", 422)
    if len(typed) > turn_pipeline.TYPED_TURN_MAX_CHARS:
        raise AppError("TEXT_TOO_LONG", f"Keep a typed message under {turn_pipeline.TYPED_TURN_MAX_CHARS} characters.", 422)
    run = await turn_pipeline.turn_begin(request, agent_id, user_id)
    run.is_test = turn_pipeline.form_flag(test_call)
    if audio is None:
        run.text_input = typed
    return await stream_turn_response(run, audio, conversation_id)


async def stream_turn_response(run: TurnRun, audio: Optional[UploadFile], conversation_id: Optional[str]) -> StreamingResponse:
    """Validate the recording and start the streaming turn of `run`. Shared by the owner's endpoint above and the
    public web call link (routers/web_call.py)."""
    file_bytes = b"" if audio is None else await validate_audio_upload(audio, allow_recording=True, field="audio")
    turn_id = str(uuid.uuid4())
    turn_stream.remember_stream_turn(turn_id, run.user_id)
    if run.local:
        await asyncio.to_thread(turn_stream.prune_sentence_clips)
    return StreamingResponse(turn_stream.turn_event_stream(run, file_bytes, conversation_id, turn_id),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/conversations/turns/{turn_id}/sentences/{index}/audio")
async def get_turn_sentence_audio(turn_id: str, index: int, request: Request,
                                  user_id: str = Depends(get_current_user)):
    """One sentence clip of a locally streamed turn . Only its owner can fetch it."""
    deps.check_id(turn_id, "turn id")
    if deps.storage_mode_of(request) != "local" or index < 0 or index > 999:
        raise AppError("NOT_FOUND", "Sentence audio not found", 404)

    def owned() -> bool:
        if turn_stream._STREAM_TURN_OWNERS.get(turn_id) == user_id:  # still streaming (the turn row is written at the end)
            return True
        turn = state.local_db.get("conversation_turns", turn_id)
        conv = state.local_db.get("conversations", turn["conversation_id"], user_id) if turn else None
        return bool(conv) and state.local_db.get("agents", conv["agent_id"], user_id) is not None

    path = os.path.join(state.LOCAL_AUDIO_DIR, f"turn_{turn_id}_{index}.wav")
    if not await asyncio.to_thread(owned) or not os.path.isfile(path):
        raise AppError("NOT_FOUND", "Sentence audio not found", 404)
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.get("/api/conversations/turns/{turn_id}/audio")
async def get_turn_audio(turn_id: str, request: Request, user_id: str = Depends(get_current_user)):
    """Reply audio of a locally stored conversation turn (cloud turns use their public storage URL)."""
    deps.check_id(turn_id, "turn id")
    if deps.storage_mode_of(request) != "local":
        raise AppError("NOT_FOUND", "Turn audio not found", 404)

    def owned() -> bool:
        turn = state.local_db.get("conversation_turns", turn_id)
        conv = state.local_db.get("conversations", turn["conversation_id"], user_id) if turn else None
        return bool(conv) and state.local_db.get("agents", conv["agent_id"], user_id) is not None

    path = os.path.join(state.LOCAL_AUDIO_DIR, f"turn_{turn_id}.wav")
    if not await asyncio.to_thread(owned) or not os.path.isfile(path):
        raise AppError("NOT_FOUND", "Turn audio not found", 404)
    return FileResponse(path, media_type="audio/wav")


# ---------------------------------------------------------------------------------------------- the live call events

@router.get("/api/agents/{agent_id}/live-events")
async def live_events(agent_id: str, request: Request, after: int = 0, conversation_id: Optional[str] = None,
                      follow: bool = True, user_id: str = Depends(get_current_user)):
    """The live call event log of one front desk as `text/event-stream` (docs/ARCHITECTURE.md, "Live call events"): every
    event after `after` (its `seq`), then new ones as they happen, for any call of this front desk: a browser web call, the
    owner's own turns or a phone call. `conversation_id` narrows it to one call. Each event is `event: <type>` and one JSON
    `data:` line with `seq`; a `: ping` comment every 15 s keeps the connection open. `follow=false` sends what there is and
    ends. Owner only."""
    from services import agent_store  # noqa: PLC0415
    await agent_store.load_agent(request, agent_id, user_id)     # raises 404 for a front desk that is not the caller's
    if conversation_id:
        deps.check_id(conversation_id, "conversation id")

    async def stream():
        cursor = max(0, after)
        idle = 0.0
        while True:
            rows = await asyncio.to_thread(call_events.since, agent_id, cursor, conversation_id)
            for e in rows:
                cursor = e["seq"]
                yield turn_stream.sse_event(e["type"], e)
            if not follow:
                return
            if await request.is_disconnected():
                return
            if rows:
                idle = 0.0
                continue
            rows = await asyncio.to_thread(call_events.wait_for, agent_id, cursor, 1.0, conversation_id)
            if not rows:
                idle += 1.0
                if idle >= 15:
                    idle = 0.0
                    yield b": ping\n\n"
            # the events found here are sent on the next pass

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/agents/{agent_id}/events")
async def agent_events(agent_id: str, request: Request, after: int = 0, wait: float = 0.0,
                       conversation_id: Optional[str] = None, user_id: str = Depends(get_current_user)):
    """The same log as live-events, as JSON `{events, last_seq, head}` (`head` is the newest seq the server has: a client whose cursor is past it restarts from 0, as after a server restart): for a browser that polls. `wait` (seconds, at most 20)
    holds the request open until an event arrives (long polling). Owner only."""
    from services import agent_store  # noqa: PLC0415
    await agent_store.load_agent(request, agent_id, user_id)
    if conversation_id:
        deps.check_id(conversation_id, "conversation id")
    cursor = max(0, after)
    wait = max(0.0, min(20.0, wait))
    rows = await asyncio.to_thread(call_events.wait_for, agent_id, cursor, wait, conversation_id) if wait else \
        await asyncio.to_thread(call_events.since, agent_id, cursor, conversation_id)
    return {"events": rows, "last_seq": rows[-1]["seq"] if rows else cursor, "head": call_events.last_seq()}


@router.get("/api/conversations/{conversation_id}/events")
async def conversation_events(conversation_id: str, request: Request, after: int = 0, agent_id: Optional[str] = None,
                              user_id: str = Depends(get_current_user)):
    """The events of one conversation after `after`, as JSON {events, last_seq}: the same log as live-events, for a client
    that polls (the phone monitor). The front desk is found from the conversation. Owner only."""
    deps.check_id(conversation_id, "conversation id")

    def work():
        conv = state.local_db.get("conversations", conversation_id, user_id)
        if conv is None:
            raise AppError("NOT_FOUND", "Conversation not found", 404)
        rows = call_events.since(conv["agent_id"], max(0, after), conversation_id)
        return {"events": rows, "last_seq": rows[-1]["seq"] if rows else max(0, after)}
    return await asyncio.to_thread(work)
