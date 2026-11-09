"""One spoken turn, step by step: the run state, speech recognition, language, conversation and retrieval, tools, caller
memory, the reply prompt, the voice plan and the stored turn. The blocking endpoint runs the steps one after another
(`turn_blocking`); the streaming turn in services/turn_stream.py runs the same steps as the reply arrives."""

import asyncio
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from fastapi import Request

try:
    from core import activity, deps, engines, state
    from core.errors import AppError
    from services import (
        agent_prompt, agent_store, asr, caller_memory, caller_profiles, canned_reply, fact_sheet, gnani, gnani_gateway, grounding,
        hearing, hinglish, knowledge, language_policy, llm, llm_limits, provenance, readback, sentences as sentences_mod, speaking,
        speech_numbers, spoken_digits, storage, synthesis, tools as tools_mod, turn_taking, voice_store, when,
    )
    from services import booking_evidence, call_events, call_notes
    from services.speaking import TtsPlan
except ImportError:  # pragma: no cover
    from backend.core import activity, deps, engines, state
    from backend.core.errors import AppError
    from backend.services import (
        agent_prompt, agent_store, asr, caller_memory, caller_profiles, canned_reply, fact_sheet, gnani, gnani_gateway, grounding,
        hearing, hinglish, knowledge, language_policy, llm, llm_limits, provenance, readback, sentences as sentences_mod, speaking,
        speech_numbers, spoken_digits, storage, synthesis, tools as tools_mod, turn_taking, voice_store, when,
    )
    from backend.services import booking_evidence, call_events, call_notes
    from backend.services.speaking import TtsPlan

log = logging.getLogger("vakya")

TURN_FALLBACK_TEXT = "I'm having trouble responding right now."


class ObservedCalls(list):
    """`run.tool_objs`: the list of this turn's tool calls, which also tells the live call log (services/call_events.py)
    about each call as it is appended (from the thread that runs the tool)."""

    def __init__(self, run: "TurnRun"):
        super().__init__()
        self._run = run

    def append(self, call: Any) -> None:
        super().append(call)
        try:
            self._run.emit_tool_end(call)
        except Exception as e:  # noqa: BLE001 - the log never breaks a turn
            log.debug("tool event skipped: %r", e)


class TurnRun:
    """State of one spoken turn, shared by the blocking endpoint and the streaming endpoint."""

    def __init__(self, request: Request, agent_id: str, user_id: str, storage_mode: Optional[str] = None):
        self.start_time = time.time()
        self.timings: Dict[str, int] = {}
        self.storage_mode = storage_mode or deps.storage_mode_of(request)
        self.local = self.storage_mode == "local"
        self.sb: Any = None
        self.agent_id = agent_id
        self.user_id = user_id
        self.is_test = False              # a simulated "Test call" by the owner: kept out of Analytics
        self.source: Optional[str] = None  # where the call came from, stored on a new conversation ("web", "phone")
        self.text_input: Optional[str] = None  # what the caller said, when it is already text (a typed question): no ASR
        self.text_only = False            # no speech synthesis at all: replies stay text (the phone rehearsal, bench/phone_rehearsal.py)
        self.reply_audio: Optional[bytes] = None  # the labelled reply audio (WAV) of a blocking turn (empty for a text-only reply)
        self.agent: dict = {}
        self.user_text = ""
        self.id_removed = False            # an identity number was taken out of what the caller said (services/id_redact.py)
        self.conversation_id: Optional[str] = None
        self.history: List[dict] = []
        self.history_known = True
        self.chunks: List[str] = []
        self.retrieval: Optional[dict] = None  # signals of the retrieval (see summarise_retrieval), stored with the turn
        self.retrieval_confidence: Optional[dict] = None  # retrieval.assess_confidence of this turn (None: unknown)
        self.language = "en"
        self.language_decision: Any = None  # language_policy.LanguageDecision of this turn (see turn_language)
        self.language_check: Optional[dict] = None  # the reply's language check: target, matched, retried
        self.tool_specs: List[Any] = []   # tools of the agent's MCP servers that are available for this turn
        self.tool_objs: List[Any] = ObservedCalls(self)   # tools_mod.ToolCall of this turn, appended as the calls are made
        self.turn_id = str(uuid.uuid4())  # the id of this exchange (the stored agent turn, its audio and its receipt)
        self.utterance_bytes: Optional[bytes] = None   # the caller's recording, kept for booking evidence (services/booking_evidence.py)
        self.caller_found: Dict[str, str] = {}         # the details the caller-details reader took from this utterance
        self.readback = False             # a booking or cancel waits for the caller's yes: the reply is its read-back
        self.asr_level: Optional[str] = None
        self.canned: Any = None           # canned_reply.CannedReply when this turn asks a clarifying question
        self.stored = False               # the turn is in the conversation's history (set by turn_store)
        self.caller_form: Dict[str, str] = {}   # what the caller has told the agent so far (services/caller_memory.py)
        self.caller_previous_agent: Optional[str] = None   # the agent's reply this turn answers (for the details reader)
        self.asr_previous_agent_text: Optional[str] = None   # a phone call: what the agent last said (what Prisma's previous_text hint follows)
        self.asr_pre: Optional["asyncio.Future"] = None      # a phone call: this recording's speech recognition, already started

    def mark(self, stage: str, since: float) -> float:
        now = time.time()
        self.timings[stage] = int((now - since) * 1000)
        return now

    # ---- the live call log (services/call_events.py): thread safe, never raises
    def emit(self, type_: str, **body: Any) -> Optional[dict]:
        return call_events.emit(self.agent_id, self.conversation_id, self.turn_id, type_, **body)

    def schedule_of(self, args: dict, value: Any = None) -> Tuple[Optional[str], Optional[str]]:
        """(id, name) of the schedule a scheduling tool call is about."""
        try:
            from services import schedule_tools  # noqa: PLC0415
            name = (value.get("schedule") if isinstance(value, dict) else None) or (args or {}).get("schedule")
            found, _problem = schedule_tools.resolve_schedule(self.agent_id, name)
            return (found["id"], found["name"]) if found else (None, None)
        except Exception:  # noqa: BLE001
            return None, None

    def emit_tool_start(self, tool: str, args: dict) -> None:
        sid, sname = self.schedule_of(args)
        self.emit("tool", call_id=f"{self.turn_id}:{len(self.tool_objs)}", **call_events.tool_event_body(
            tool, args, None, None, schedule_id=sid, schedule_name=sname, phase="start"))

    def emit_tool_end(self, call: Any) -> None:
        sid, sname = self.schedule_of(call.arguments, call.value)
        body = call_events.tool_event_body(call.name, call.arguments, call.ok, call.value, schedule_id=sid, schedule_name=sname)
        if body.get("status") in ("readback", "confirm"):
            self.readback = True
        self.emit("tool", call_id=f"{self.turn_id}:{len(self.tool_objs) - 1}", **body)

    def emit_caller(self) -> None:
        """The caller's utterance with its language, once the conversation is known (before any tool runs)."""
        text = call_events.mask_numbers(self.user_text)
        info = call_events.utterance_language(text, self.language, self.asr_level)
        self.emit("caller", text=text, typed=self.text_input is not None, **info)

    def emit_agent(self, index: int, text: str, audio_ms: Optional[int], *, disclosure: bool = False) -> None:
        text = call_events.mask_numbers(text)
        lang = call_events.utterance_language(text, self.language)
        self.emit("agent", index=index, text=text, audio_ms=audio_ms, readback=bool(self.readback and not disclosure),
                  disclosure=disclosure, language=lang["language"], script=lang["script"], words=lang["words"])


def form_flag(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


async def turn_begin(request: Request, agent_id: str, user_id: str, *, storage_mode: Optional[str] = None,
                      agent: Optional[dict] = None, source: Optional[str] = None) -> TurnRun:
    """`storage_mode`, `agent` and `source` are for callers that are not the agent's owner's own request (the public
    web call link): they already looked the agent up and know which store holds it."""
    activity.mark_turn()   # background jobs wait while conversations are going on
    run = TurnRun(request, agent_id, user_id, storage_mode)
    run.source = source
    # Gnani requests of this turn: phone and web calls are the "live" lane (never held back by the budget reserve, first in the
    # queue); a desk or Talk-page turn is "desk". Their credits are attributed to the conversation once turn_context knows it.
    gnani_gateway.begin_scope("live" if source in ("phone", "web", "missed_call") else "desk", user_id=user_id)
    run.agent = agent if agent is not None else await agent_store.load_agent(request, agent_id, user_id)
    run.sb = None if run.local else storage.require_supabase()
    return run


def gnani_turn_hints(run: Any, hints: dict, qu: Any) -> None:
    """What Prisma is told about this turn: the agent's languages, its names and terms as keyterms (the entities the
    agent's documents mention), what the caller said last (`previous_text`), and, from services/hearing.py, what the desk
    has learnt (its vocabulary and substitution rules), what the caller is about to say (`expecting`: a name, a day or
    time, digits, a yes or no, read from the desk's last reply) with a matching `topic`, the names already heard on this
    call (`speaker_names`) and the request id (`X-API-Request-ID`)."""
    hints["asr_languages"] = [str(x) for x in ((run.agent or {}).get("supported_languages") or [])]
    entities: List[str] = []
    try:
        profile = qu.get_profile(run.agent, run.user_id, run.storage_mode)
        entities = gnani.keyterms_from_entities(profile.lexicon.entities) if profile is not None else []
    except Exception as e:  # noqa: BLE001 - a hint, never a reason to fail the turn
        log.debug("no keyterms for this turn: %s", e)
    hints["keyterms"] = entities
    previous = getattr(run, "asr_previous_user_text", None)
    if not previous:
        previous = next((h.get("text_content") for h in reversed(getattr(run, "history", None) or [])
                         if h.get("role") == "user"), None)
    hints["previous_text"] = previous or None
    try:
        hearing.prepare_turn_hints(run, hints, entities, env_terms=len(gnani.env_keyterms()))
    except Exception as e:  # noqa: BLE001 - what the desk has learnt helps; it never fails a turn
        log.debug("no hearing hints for this turn: %r", e)


async def asr_for_turn(run: Any, file_bytes: bytes) -> Tuple[str, dict]:
    """The speech recognition of one turn's recording (Gnani Prisma): (text, the hints it was made with). `run` needs agent,
    user_id and storage_mode. Raises AppError: "Needs a Gnani key", or Gnani's failure with the gateway's plan in its
    details. The phone call also calls it ahead of the turn (see turn_asr, `run.asr_pre`)."""
    qu = asr.qu_module()
    # The agent's languages, its names and terms as keyterms and what the caller said last.
    hints = qu.turn_asr_hints(run.agent, run.user_id, run.storage_mode)
    gnani_turn_hints(run, hints, qu)
    token = qu.ASR_CTX.set(hints)
    try:
        user_text = await asr.speech_to_text(file_bytes, "turn", qu.asr_language(run.agent))
    finally:
        qu.ASR_CTX.reset(token)
    return user_text, hints


async def asr_from_stream(run: Any, text: str, language_code: Optional[str]) -> Tuple[str, dict]:
    """The speech recognition of a phone turn that the call's realtime stream already made (services/phone_asr.py):
    (text, hints) as `asr_for_turn` returns them, without a request. The hints are the turn's own (for the confidence
    step, the learnt names and the events); Prisma was not given them, which is the price of the stream (it cannot carry
    keyterms or the desk's corrections)."""
    qu = asr.qu_module()
    hints = qu.turn_asr_hints(run.agent, run.user_id, run.storage_mode)
    gnani_turn_hints(run, hints, qu)
    fixed = gnani.collapse_spelled_acronyms(text or "")
    hints["detected_language"] = gnani.two_letter(language_code)
    hints["detected_language_code"] = language_code
    hints["prompt_used"] = False
    hints["segments"] = None
    hints["provider"] = "gnani"
    hints["streamed"] = True
    hearing.after_transcript(hints, fixed)
    return fixed, hints


def _clean_heard(run: Any, text: str) -> str:
    """What the caller said, without identity numbers (Aadhaar, PAN, card, OTP: services/id_redact.py). Everything after
    this (the stored turn, the events, the kept utterance, the caller details, the logs and the model) sees the cleaned
    text; `run.id_removed` tells the evidence not to keep the clip of this utterance, which has the number in its sound."""
    cleaned = call_events.redact_ids(text)
    run.id_removed = cleaned != text
    if run.id_removed:
        log.info("turn: an identity number in the caller's words was removed before anything was stored")
    return cleaned


async def turn_asr(run: TurnRun, file_bytes: bytes) -> float:
    """Speech to text (Gnani Prisma), off the event loop so other requests keep flowing.
    Sets run.user_text and run.language; returns the time the next stage's clock starts from."""
    t = time.time()
    run.asr_confidence = None   # {"level": "high"|"medium"|"low"|"unknown", ...} (query_understanding.asr_confidence)
    run.clarify_text = None     # set: the turn asks this short question instead of answering (see turn_context)
    run.utterance_bytes = file_bytes or None   # kept for the evidence of a booking (services/booking_evidence.py)
    if run.text_input is not None:  # a typed message, or a transcript the caller of the turn already made
        run.user_text = _clean_heard(run, run.text_input)
        run.search_query = run.user_text
        run.language = agent_prompt.detect_reply_language(run.user_text, run.agent)
        run.timings.setdefault("asr_ms", 0)
        return t
    qu = asr.qu_module()
    pre = getattr(run, "asr_pre", None)
    if pre is not None:
        # A phone call already sent this very recording to speech recognition (to see whether the caller had finished
        # giving details, routers/phone.py): its result is this turn's, with the hints it was made under.
        try:
            user_text, hints = await (asyncio.shield(pre) if isinstance(pre, asyncio.Future) else pre)
            h = (hints or {}).get("_hearing")
            if isinstance(h, dict) and not callable(h.get("emit")) and callable(getattr(run, "emit", None)):
                h["emit"] = run.emit        # made before the turn began: its "Listening for" event goes out now
            hearing.emit_listening(hints or {})
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - recognise it again
            log.warning("the earlier speech recognition of this recording failed (%r); doing it again", e)
            user_text, hints = await asr_for_turn(run, file_bytes)
    else:
        user_text, hints = await asr_for_turn(run, file_bytes)
    if user_text is None:
        raise AppError("ASR_UNAVAILABLE", "Speech recognition is unavailable (Gnani did not answer).", 503)
    if not user_text:
        raise AppError("NO_SPEECH", "No speech detected in the recording.", 422)
    run.user_text = user_text = _clean_heard(run, user_text)
    run.search_query = user_text
    run.language = agent_prompt.detect_reply_language(user_text, run.agent)
    next_t = run.mark("asr_ms", t)
    try:   # confidence and the clarification decision (pure Python, well under a millisecond)
        run.asr_confidence = qu.asr_confidence(hints.get("segments"), user_text)
        run.asr_level = (run.asr_confidence or {}).get("level")
        profile = qu.get_profile(run.agent, run.user_id, run.storage_mode)
        lexicon = profile.lexicon if profile is not None else None
        changes = qu.snap_entities(user_text, lexicon)[1] if (lexicon is not None and qu.snap_enabled()) else []
        kind = qu.should_clarify(run.asr_confidence, user_text, lexicon, changes, source=getattr(run, "source", None))
        if kind:
            run.clarify_kind = kind
            run.clarify_text = qu.clarification_text(kind, run.language)
        if run.asr_confidence.get("level") in ("low", "medium") or kind:
            log.info("ASR confidence %s (avg_logprob %s, no_speech %s, prompt %s)%s", run.asr_confidence["level"],
                     run.asr_confidence.get("avg_logprob"), run.asr_confidence.get("no_speech_prob"),
                     hints.get("prompt_used"), f": will ask a {kind} clarification" if kind else "")
    except Exception as e:  # noqa: BLE001 - never fails a turn
        log.warning("ASR confidence step failed: %r", e)
    return next_t


HISTORY_TURNS = 6  # the latest rows the language model always sees as they were said, whatever the token budget


def _history_limit(run) -> int:
    """How many rows are read from the store for a turn: the call so far, up to services/call_notes.py FETCH_MAX. What the
    model is shown of them is decided by `call_notes.window` (the whole call when it fits CALL_HISTORY_TOKENS, else the
    latest turns plus call notes that keep every captured detail), so a name given in turn 2 is still there in turn 29."""
    return call_notes.FETCH_MAX


async def turn_language(run: TurnRun, stored: Optional[str]) -> None:
    """The reply language of this turn (services/language_policy.py): the caller's script, an explicit request for a
    supported language (remembered on the conversation, `stored` is what an earlier turn remembered), or a short
    turn that continues the language of the agent's last reply. Sets run.language and run.language_decision."""
    previous = next((h["text_content"] for h in reversed(run.history) if h["role"] == "agent"), None)
    supported = (getattr(run, "agent", None) or {}).get("supported_languages") or ["en"]
    decision = language_policy.decide(run.user_text, supported, stored, previous)
    run.language, run.language_decision = decision.target, decision
    if not (decision.choice_changed and decision.choice and run.conversation_id and run.history_known):
        return
    try:
        if run.local:
            await asyncio.to_thread(state.local_db.update, "conversations", run.conversation_id,
                                    {"reply_language": decision.choice}, run.user_id)
        else:
            await asyncio.to_thread(lambda: run.sb.table("conversations").update({"reply_language": decision.choice})
                                    .eq("id", run.conversation_id).execute())
    except Exception as e:  # noqa: BLE001 - migration 014 may not be applied: the choice then lasts one turn only
        log.warning("could not remember the language choice (apply migration 014_conversation_reply_language.sql): %s", e)


def _language_regen(run: TurnRun, messages: List[dict], draft: Optional[str], timeout: float) -> str:
    """Blocking: the one regeneration after a reply in the wrong language. With a `draft` the model is asked to say
    it again in the target language (what the tools did stays as it was); without one, to answer again."""
    msgs = language_policy.regen_messages(messages, agent_prompt.LANGUAGE_NAMES.get(run.language, run.language), draft)
    return (llm.chat(msgs, temperature=0.3, max_tokens=160, timeout=timeout).text or "").strip()


def language_known_text(messages: List[dict]) -> str:
    """What the caller said this turn (the part of the last message after "Caller says:"): Latin words the caller used,
    such as "MRI" or "fees", do not count against a reply in an Indian script (language_policy.reply_matches). The
    passages are left out: they are in English, so nearly every English word would be "known"."""
    if not messages or messages[-1].get("role") != "user":
        return ""
    content = str(messages[-1].get("content") or "")
    return content.rsplit("Caller says:", 1)[-1]


def enforce_language(run: TurnRun, messages: List[dict], reply: str, timeout: float) -> str:
    """Blocking: keep the reply when it is in the turn's language, else regenerate once (see language_policy.enforce)."""
    text, info = language_policy.enforce(run.language, reply, lambda: _language_regen(run, messages, reply, timeout),
                                         known_text=language_known_text(messages))
    run.language_check = info
    if info["retried"]:
        log.info("reply was not in %s; regenerated once (%s)", run.language,
                 "fixed" if info["retry_matched"] else "kept the first reply")
    return text


async def turn_context(run: TurnRun, conversation_id: Optional[str], t: float) -> float:
    """Conversation (created or checked for ownership), its recent history, and RAG retrieval.

    When speech recognition was unsure (see turn_asr) the turn asks one short clarifying question instead
    (run.canned) and retrieval is skipped."""
    local, sb, agent_id, user_id = run.local, run.sb, run.agent_id, run.user_id
    history_known = True
    stored_language: Optional[str] = None  # the language the caller asked for earlier in this conversation
    try:
        if local:
            if not conversation_id:
                conv = await asyncio.to_thread(state.local_db.insert, "conversations",
                                               {"agent_id": agent_id, "user_id": user_id,
                                                **({"is_test": True} if run.is_test else {}),
                                                **({"source": run.source} if run.source else {})})
                conversation_id = conv["id"]
            else:
                own = await asyncio.to_thread(state.local_db.get, "conversations", conversation_id, user_id)
                if own is None or own.get("agent_id") != agent_id:
                    raise AppError("NOT_FOUND", "Conversation not found", 404)
                stored_language = own.get("reply_language")
            turns = await asyncio.to_thread(
                state.local_db.select_last, "conversation_turns", lambda r: r.get("conversation_id") == conversation_id,
                _history_limit(run))
            history = [{"role": r["role"], "text_content": r["text_content"], "tool_calls": r.get("tool_calls"),
                        "created_at": r.get("created_at")} for r in turns]
        else:
            if not conversation_id:
                conv_row = {"agent_id": agent_id, "user_id": user_id}
                flags = {**({"is_test": True} if run.is_test else {}), **({"source": run.source} if run.source else {})}
                try:
                    conv_res = await asyncio.to_thread(
                        lambda: sb.table("conversations").insert({**conv_row, **flags}).execute())
                except Exception as e:  # noqa: BLE001 - migration 010 (is_test) or 013 (source) may not be applied yet
                    if not flags:
                        raise
                    log.warning("could not flag the conversation (apply migrations 010 and 013): %s", e)
                    conv_res = await asyncio.to_thread(lambda: sb.table("conversations").insert(conv_row).execute())
                conversation_id = conv_res.data[0]["id"]
            else:
                try:
                    own = await asyncio.to_thread(lambda: sb.table("conversations").select("id, reply_language")
                                                  .eq("id", conversation_id).eq("agent_id", agent_id).execute())
                except Exception:  # noqa: BLE001 - migration 014 (reply_language) may not be applied yet
                    own = await asyncio.to_thread(lambda: sb.table("conversations").select("id")
                                                  .eq("id", conversation_id).eq("agent_id", agent_id).execute())
                if not own.data:
                    raise AppError("NOT_FOUND", "Conversation not found", 404)
                stored_language = own.data[0].get("reply_language")
            def read_history(columns: str):
                return (sb.table("conversation_turns").select(columns)
                        .eq("conversation_id", conversation_id)
                        .order("created_at", desc=True)
                        .limit(_history_limit(run))
                        .execute())
            history_res = await asyncio.to_thread(read_history, "role, text_content, created_at")
            history = list(reversed(history_res.data or []))
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("conversation history unavailable: %s", e)
        history = []
        history_known = bool(conversation_id) is False  # unknown history of an existing call: do not repeat the line
        conversation_id = conversation_id or str(uuid.uuid4())
    run.conversation_id, run.history, run.history_known = conversation_id, history, history_known
    gnani_gateway.bind_conversation(run.conversation_id)
    await turn_language(run, stored_language)
    previous_reply = next((h["text_content"] for h in reversed(run.history) if h["role"] == "agent"), None)
    if getattr(run, "clarify_text", None):
        # Speech recognition was unsure (see turn_asr): ask one short question in the reply language instead of
        # answering, spoken as a canned reply (no retrieval, no model). Never twice in a row.
        clarify = asr.qu_module().clarification_answer(agent_id, getattr(run, "clarify_kind", "generic"), run.language,
                                             previous_reply, run.conversation_id)
        if clarify is not None:
            run.clarify_text, run.canned = clarify.text, clarify
            run.chunks, run.retrieval = [], None
            run.timings["retrieval_ms"] = 0
            return time.time()
        run.clarify_text = None
    asr.qu_module().note_answered(run.conversation_id)
    # What to search for: the transcript with the agent's names snapped, a follow-up condensed into a standalone
    # question (services/query_understanding.py). The language model still sees run.user_text.
    skip = await retrieval_skip_reason(run, previous_reply)
    if skip:
        # A booking step ("Monday", "five pm", "yes", a name, a number) or an agent with no documents has nothing to look up:
        # the embedding call and the search (550-1000 ms on the 8 Nov 2026 call) are left out.
        run.search_query = run.user_text
        run.chunks, run.retrieval, run.retrieval_confidence = [], {**knowledge.summarise_retrieval([]), "skipped": skip}, None
        run.timings["retrieval_ms"] = 0
        return time.time()
    run.search_query, _query_info = await asr.qu_module().turn_search_query(
        getattr(run, "agent", None), user_id, run.storage_mode, run.user_text, run.history,
        answering_agent=_asks_for_detail(previous_reply))
    signals: dict = {}
    run.chunks = await knowledge.retrieve_context(agent_id, run.search_query or run.user_text, user_id=user_id,
                                        storage_mode=run.storage_mode, signals=signals)
    run.retrieval = signals or None
    run.retrieval_confidence = signals.get("confidence")   # contract 2: read by services/grounding.py
    return run.mark("retrieval_ms", t)


# A caller who is asking for information, not answering a booking question
_INFO_QUESTION = re.compile(
    r"\b(?:what|which|how|where|why|who|whom|whose|price|prices|cost|costs|fee|fees|charge|charges|insurance|address|location|"
    r"timing|timings|hours|policy|policies|offer|offers|services?|kya|kaise|kahan|kahaan|kyun|kyon|kitna|kitne|kitni|kaun|kab|"
    r"fees|rate|rates)\b|क्या|कैसे|कहाँ|कहां|क्यों|कितना|कितने|कितनी|कौन|कब|काय|कुठे|किती|कसे|फीस|शुल्क|पता",
    re.IGNORECASE)
_BOOK_INTENT = re.compile(r"\b(?:book|booking|appointment|appointments|slot|schedule|reschedule|cancel|postpone)\b|बुक|अपॉइंटमेंट|अपॉइन्टमेंट|एपॉइंटमेंट",
                         re.IGNORECASE)
def booking_request(user_text: str) -> bool:
    """Does the caller ask to book something ("appointment", "book", "बुक", "अपॉइंटमेंट")?"""
    return bool(_BOOK_INTENT.search(user_text or ""))


MAX_STEP_WORDS = 8
_BOOKING_STATUS = re.compile(r"\b(?:did you (?:do|book|make|confirm|finish)|is it (?:done|booked|confirmed)|have you (?:done|booked|confirmed)|"
                             r"(?:it|that|booking) (?:is|got) (?:done|booked|confirmed))\b", re.IGNORECASE)


def booking_step(user_text: str, previous_reply: Optional[str]) -> bool:
    """Is what the caller said a step of a booking, not a question for the documents: a yes or a no, a day, a date, a time, a
    phone number, or the short answer to a detail the agent just asked for (a name)? Short, and never an information
    question ("what are your fees", "kya aap ... ?")."""
    text = (user_text or "").strip()
    words = re.findall(r"[\w\u0900-\u097F]+", text)
    if not words or len(words) > MAX_STEP_WORDS + 4 or _INFO_QUESTION.search(text):
        return False
    if _BOOK_INTENT.search(text):    # "I want to book an appointment": the tools answer it
        return True
    if len(words) > MAX_STEP_WORDS:
        return False
    if (len(words) <= 6 and readback.is_affirmative(text)) or (len(words) <= 3 and readback.leading_yes(text)):
        return True              # "Yes, I already said yes" is a yes to the read-back, not a question for the documents
    if _BOOKING_STATUS.search(text):
        return True              # "Did you do it?", "is it booked?": about the booking just talked of
    if re.search(r"\b(?:no|nope|nahi|nahin|nai)\b|नहीं|नही|नाही", text, re.IGNORECASE) and len(words) <= 3:
        return True
    if when.time_mentions(text) or when.resolve_date(text, None).date is not None or spoken_digits.find_mobile(text):
        return True
    return len(words) <= 5 and turn_taking.asks_for_detail(previous_reply)


async def retrieval_skip_reason(run: TurnRun, previous_reply: Optional[str]) -> Optional[str]:
    """Why this turn needs no knowledge retrieval, or None: "no_documents" (the agent has none uploaded; local store, where
    it is known) or "booking_step" (an agent with booking tools and a caller who is giving a booking detail). RETRIEVAL_SKIP=off
    turns it off."""
    if os.getenv("RETRIEVAL_SKIP", "on").strip().lower() in ("off", "0", "false", "no"):
        return None
    try:
        if run.local and not await asyncio.to_thread(
                lambda: bool(state.local_db.select("documents", lambda r: r.get("agent_id") == run.agent_id, limit=1))):
            return "no_documents"
        from services import schedule_tools  # noqa: PLC0415
        has_tools = bool((run.agent or {}).get("tools")) or schedule_tools.has_schedules(run.agent_id)
        if has_tools and booking_step(run.user_text, previous_reply):
            return "booking_step"
    except Exception as e:  # noqa: BLE001 - a doubt means: retrieve as before
        log.debug("retrieval skip check failed: %r", e)
    return None


# The agent's previous reply asked the caller for a detail (a name, a number, a date...): the caller's next words answer
# it, so the search query is not condensed as a fresh question. The word lists and the check live in
# services/turn_taking.py, which the phone call also uses to decide how long to wait for that answer.
_asks_for_detail = turn_taking.asks_for_detail


async def canned_audio(hit: Any, plan: "TtsPlan", text: str, render) -> Tuple[np.ndarray, int, bool]:
    """The unlabelled audio of `text` (the canned reply, or one sentence of it) in `plan`'s voice: from the audio cache
    when this reply was spoken before in this voice, else rendered by `render()` (an async callable returning
    (audio, sample rate)) and kept. Each turn labels its own copy. Returns (audio, rate, was cached)."""
    loop = asyncio.get_running_loop()

    def render_blocking():
        wav, sr = asyncio.run_coroutine_threadsafe(render(), loop).result(timeout=300)
        return synthesis.to_numpy(wav), sr
    voice = plan.voice_id if plan.clone_required else None
    (wav, sr), was_cached = await asyncio.to_thread(canned_reply.cached_audio, hit, render_blocking, plan.engine_key,
                                                    voice, plan.tts_language, text)
    return wav, sr, was_cached


def canned_info(hit: Any, cached: List[bool]) -> dict:
    """What the turn's response says about the canned reply that answered it."""
    return {"id": hit.id, "audio_cached": bool(cached) and all(cached)}


async def turn_tools(run: TurnRun) -> float:
    """Resolve the tools of the agent's MCP servers (starting a server on first use). An agent without tools, or whose
    servers cannot start, answers the normal way. Returns the time it took, which counts as tools time."""
    names = [str(n) for n in (run.agent.get("tools") or [])]
    from services import schedule_tools  # noqa: PLC0415
    agent_id = run.agent.get("id")
    with_schedules = "schedules" in names or (await asyncio.to_thread(schedule_tools.has_schedules, agent_id))
    if not names and not with_schedules:
        return 0.0
    started = time.time()
    run.tool_specs = await asyncio.to_thread(tools_mod.hub.specs_for, names)
    if with_schedules:   # a front desk that has schedules always has the scheduling tools, in place of the doctor list
        run.tool_specs = [s for s in run.tool_specs if s.server != "appointments"] + \
            [_observed(s, run) for s in schedule_tools.specs_for_agent(agent_id, tools_mod)]
    if "call" in names and run.source == "phone":    # end_call / transfer_call exist only on a live phone call
        from services import call_control  # noqa: PLC0415
        run.tool_specs = list(run.tool_specs) + call_control.specs_for_run(run, tools_mod)
    if run.tool_specs:   # an agent whose tools could not start answers without any; otherwise "forget me" (services/caller_profiles.py)
        run.tool_specs = list(run.tool_specs) + caller_profiles.forget_specs(run, tools_mod)
    return time.time() - started


def _observed(spec: Any, run: TurnRun) -> Any:
    """The scheduling tool `spec` with its runner wrapped, so the live call log hears "checking Thu 6 pm" the moment the tool
    starts (the end of the call is reported when the call is recorded: see ObservedCalls)."""
    inner = spec.runner

    def runner(tool: str, args: dict) -> Tuple[bool, Any]:
        try:
            run.emit_tool_start(tool, args)
        except Exception as e:  # noqa: BLE001
            log.debug("tool start event skipped: %r", e)
        token = _bind_whatsapp_call(getattr(run, "conversation_id", None))
        audit_token = _bind_audit_call(getattr(run, "conversation_id", None))
        try:
            return inner(tool, args)
        finally:
            for t in (token, audit_token):
                if t is not None:
                    t.var.reset(t)
    spec.runner = runner
    return spec


def _bind_audit_call(conversation_id: Any) -> Any:
    """Tie the audit lines of a booking, move or cancel made by this tool to this call (services/booking_audit.py)."""
    try:
        from services import booking_audit  # noqa: PLC0415
        return booking_audit.bind_call(conversation_id)
    except Exception:  # noqa: BLE001
        return None


def _bind_whatsapp_call(conversation_id: Any) -> Any:
    """Tie a booking, move or cancel made by this tool to this call, so its WhatsApp confirmation goes when this call ends
    (services/booking_notify.py). Returns the contextvar token to reset, or None."""
    if not conversation_id:
        return None
    try:
        from services import booking_notify  # noqa: PLC0415
        return booking_notify.bind_call(str(conversation_id))
    except Exception:  # noqa: BLE001
        return None


def tool_turn(run: TurnRun, messages: List[dict], timeout: float) -> "tools_mod.ToolTurn":
    """Blocking: the model answers with the agent's tools (see services/tools.py). The confirmation rule for
    destructive tools looks at the agent's previous reply of this conversation."""
    previous = next((h["text_content"] for h in reversed(run.history) if h["role"] == "agent"), None) \
        if run.history_known else None
    known = " ".join(h["text_content"] for h in run.history) + " " + run.user_text
    return tools_mod.run_tool_turn(messages, run.tool_specs, previous_agent_text=previous, known_text=known,
                                   timeout=timeout, calls=run.tool_objs,
                                   known_slots=tools_mod.known_slots(run.history, getattr(run, "dropped_slots", ())), caller_text=run.user_text)


def tool_records(run: TurnRun) -> List[dict]:
    """What the tools did this turn, for the turn record and the Talk page: name, arguments, ok, result summary.
    Also kept when the reply itself failed: an action that was taken must not vanish from the record."""
    return [c.record() for c in run.tool_objs]


def _tools_section(run: TurnRun) -> str:
    """The tools part of the system prompt (empty without tools): static per agent, see agent_system_prompt."""
    if not run.tool_specs:
        return ""
    return tools_mod.tools_prompt(run.tool_specs, json_protocol=tools_mod.tool_protocol() != "native")


def turn_slots(run: TurnRun) -> Dict[str, str]:
    """The details the caller already gave in this conversation, for every turn (CALLER_MEMORY, default on): the caller
    details form with the arguments of earlier tool calls on top. Empty with CALLER_MEMORY=off: then only an agent with
    tools is told, by the tool loop, and only what its tool calls had."""
    if not caller_memory.enabled():
        return {}
    return caller_memory.prompt_slots(getattr(run, "caller_form", None), tools_mod.known_slots(run.history, getattr(run, "dropped_slots", ())),
                                      caller_memory.schema_for(getattr(run, "agent", None)))


async def turn_caller_memory(run: TurnRun) -> None:
    """Read what the caller said this turn into the conversation's caller details (rules only: microseconds), on top of
    what the earlier turns left there. Sets run.caller_form, which the reply is written with. Nothing is stored yet: a
    turn that is cancelled (a speculative phone turn) changes nothing, `turn_memory_commit` stores it once the turn is.
    Never fails a turn."""
    if not caller_memory.enabled() or not run.user_text or not run.conversation_id:
        return
    try:
        stored = caller_memory.cached(run.conversation_id)   # in memory during a call: no thread, no waiting
        if stored is None:
            stored = await asyncio.to_thread(caller_memory.load, run.conversation_id, state.local_db if run.local else None,
                                             run.user_id)
        previous = next((h["text_content"] for h in reversed(run.history) if h["role"] == "agent"), None) \
            if run.history_known else None
        run.caller_previous_agent = previous
        found = caller_memory.extract(run.user_text, previous, schema=caller_memory.schema_for(getattr(run, "agent", None)))
        run.caller_found = dict(found)   # which details this very utterance supplied (booking evidence)
        run.caller_form = caller_memory.merge(stored, found)
        if found.get("date") and not found.get("time") and tools_mod.last_check_refused(run.history):
            # a new day after a time that was not free: that time does not come along (it was the reason for the offer)
            run.caller_form.pop("time", None)
            run.dropped_slots = ("time",)
        if found:
            log.info("caller details: read %s from the caller's words (conversation %s)", ", ".join(sorted(found)),
                     run.conversation_id)
    except Exception as e:  # noqa: BLE001
        log.warning("caller details could not be read this turn: %r", e)


def link_bookings(run: TurnRun, receipt_id: Optional[str]) -> None:
    """Blocking. A booking the agent made this turn is tagged in the appointments store with the conversation, the
    turn's signed receipt and the agent, so the desk can open the call behind it. With local storage the evidence of the
    booking is sealed first (services/booking_evidence.py): the booking then points at its own signed receipt (kind
    "booking", which covers the evidence), and `reply_receipt_id` keeps the receipt of this turn's spoken reply. The live
    call log hears about the booking. Never raises."""
    try:
        for c in run.tool_objs:
            value = getattr(c, "value", None)
            if c.name in ("book_appointment", "book") and c.ok and isinstance(value, dict) and value.get("booking_id"):
                from mcp_servers import appointments  # noqa: PLC0415
                row = next((b for b in appointments.all_bookings() if b.get("booking_id") == value["booking_id"]), None) or value
                sealed = booking_evidence.seal_booking(run, row, getattr(run, "turn_id", None), receipt_id) \
                    if getattr(run, "turn_id", None) else None
                appointments.annotate(value["booking_id"], conversation_id=run.conversation_id,
                                      receipt_id=(sealed or {}).get("receipt_id") or receipt_id, agent_id=run.agent_id,
                                      evidence_id=(sealed or {}).get("evidence_id"),
                                      reply_receipt_id=receipt_id if sealed else None)
                if hasattr(run, "emit"):
                    run.emit("booking", booking_id=value["booking_id"], name=row.get("name"), schedule_id=row.get("schedule_id"),
                             schedule=row.get("schedule_name"), date=row.get("date"), time=row.get("time"), start=row.get("start"),
                             evidence_id=(sealed or {}).get("evidence_id"),
                             receipt_id=(sealed or {}).get("receipt_id") or receipt_id,
                             evidence_complete=(sealed or {}).get("complete"))
    except Exception as e:  # noqa: BLE001
        log.warning("could not link a booking to its call: %r", e)


def turn_commit_utterance(run: TurnRun, turn_id: str) -> None:
    """Keep the caller's words and recording of this committed turn for a possible booking, in the background."""
    try:
        if booking_evidence.wants_audio(run) and run.conversation_id:
            run.utterance_task = asyncio.ensure_future(asyncio.to_thread(booking_evidence.commit_utterance, run, turn_id))
    except Exception as e:  # noqa: BLE001
        log.debug("utterance not kept: %r", e)


async def turn_link_bookings(run: TurnRun, receipt_id: Optional[str]) -> None:
    if any(getattr(c, "name", "") in ("book_appointment", "book") and getattr(c, "ok", False) for c in run.tool_objs):
        task = getattr(run, "utterance_task", None)
        if task is not None:
            try:
                await task    # the caller's yes must be on file before the evidence is gathered
            except Exception:  # noqa: BLE001
                pass
        await asyncio.to_thread(link_bookings, run, receipt_id)


def turn_memory_commit(run: TurnRun) -> None:
    """The turn is stored: keep its caller details (in memory at once, in the store in the background) and, when a small
    model is available, ask it for what the rules cannot read, in the background: its answer lands for the next turn.
    Nothing here waits for anything, so the reply is never delayed. Never raises."""
    try:
        if not caller_memory.enabled() or not run.conversation_id:
            return
        cid, form = run.conversation_id, dict(getattr(run, "caller_form", None) or {})
        db = state.local_db if run.local else None
        loop = asyncio.get_running_loop()
        if form:
            caller_memory.remember(cid, form)
            loop.run_in_executor(None, caller_memory.save, cid, form, db, run.user_id)
        if run.user_text and caller_memory.llm_enabled() and caller_memory.llm_worth_reading(run.user_text, getattr(run, "caller_found", None)):
            loop.create_task(_turn_memory_llm(cid, db, run.user_id, run.caller_previous_agent, run.user_text,
                                              caller_memory.schema_for(getattr(run, "agent", None))))
    except Exception as e:  # noqa: BLE001
        log.warning("caller details could not be kept: %r", e)


async def _turn_memory_llm(conversation_id: str, db: Any, user_id: str, agent_text: Optional[str], caller_text: str,
                           schema: Optional[Dict[str, Any]] = None) -> None:
    try:
        got = await asyncio.to_thread(caller_memory.llm_read, agent_text, caller_text, schema)
        if not got:
            return
        latest = await asyncio.to_thread(caller_memory.load, conversation_id, db, user_id)
        merged = caller_memory.merge_llm(latest, got, None, schema)
        if merged != latest:
            added = sorted(k for k in merged if merged.get(k) != latest.get(k))
            await asyncio.to_thread(caller_memory.save, conversation_id, merged, db, user_id)
            log.info("caller details: the small model added %s (conversation %s)", ", ".join(added), conversation_id)
    except Exception as e:  # noqa: BLE001 - a missing reading is never worth anything louder
        log.debug("caller details model reading skipped: %r", e)


def turn_messages(run: TurnRun) -> List[dict]:
    """The chat messages the language model answers: a static system prompt (identical on every turn of the agent), the
    history as it was said, then the caller's message with the retrieved passages, the time (for an agent with tools),
    the reply language of this turn and the caller's words. See agent_system_prompt and turn_user_content."""
    supported = run.agent.get("supported_languages") or ["en"]
    messages = [{"role": "system",
                 "content": agent_prompt.agent_system_prompt(run.agent, None, run.language, with_tools=bool(run.tool_specs),
                                                 language_decision=run.language_decision,
                                                 tools_text=_tools_section(run), language_in_message=True)}]
    slots = turn_slots(run)
    shown, notes = call_notes.window(run.history, conversation_id=getattr(run, "conversation_id", None), slots=slots)
    messages += [{"role": "assistant" if h["role"] == "agent" else "user", "content": h["text_content"]}
                 for h in shown]   # the whole call, or its latest turns with `notes` (services/call_notes.py) for the rest
    content = agent_prompt.turn_user_content(
        run.user_text, run.chunks, with_time=bool(run.tool_specs), facts=fact_sheet.turn_facts(run),
        slots=slots,
        reply_line=language_policy.turn_note(run.language_decision, supported,
                                             agent_prompt.LANGUAGE_NAMES.get(run.language, run.language)))
    known = caller_profiles.prompt_block(getattr(run, "conversation_id", None))   # a returning phone caller (services/caller_profiles.py)
    from services import delays  # noqa: PLC0415
    late = delays.prompt_block(run.agent.get("id"), getattr(run, "conversation_id", None))   # running late today, and a call the desk placed to say so (services/delays.py)
    extra = "\n".join(x for x in (notes, known, late) if x)
    messages.append({"role": "user", "content": content + ("\n" + extra if extra else "")})
    return messages


def _guard_split(text: str) -> List[str]:
    """A finished reply cut into the pieces the number guard checks (the streamed turn's sentences)."""
    splitter = sentences_mod.turn_splitter(first_phrase=False)
    return [*splitter.feed(text), *splitter.flush()]


def turn_discloses(run: TurnRun) -> bool:
    """The agent says it is an AI at the start of a call: only on the first agent reply of a conversation."""
    return (run.agent.get("disclose_ai") is not False and run.history_known
            and not any(h["role"] == "agent" for h in run.history))


async def turn_spoken(language: str, text: str, fallback: bool, *, use_llm: bool = True,
                       converted: Optional[str] = None) -> Tuple[str, str]:
    """(text to synthesize, language the TTS engines see).

    Hinglish is spoken by the Hindi voices, which read Devanagari: the reply is converted for speech only, the text
    shown and stored stays as the agent wrote it, and the engines see "hi", never "hinglish". `use_llm=False` uses the
    dictionary only (no model call, microseconds); `converted` is a Devanagari rendering that is already available (the
    streaming turn converts the later sentences of a reply in the background) and is used as it is. Hindi speech gets
    its numbers in the form the Hindi voice reads correctly (speech_numbers.hindi_numbers_for_speech)."""
    tts_language = "hi" if language == "hinglish" else language
    text = speech_numbers.spoken_dates(text)   # "2026-11-09" and a year after a month are never said aloud
    spoken_text = text
    if fallback and language == "hinglish":
        tts_language = "en"  # the canned fallback line is English
    elif tts_language == "hi" and hinglish.is_roman_script(text):
        if converted:
            spoken_text = converted
        elif not use_llm:
            spoken_text = hinglish.dictionary_to_devanagari(text)
        else:
            conv_timeout = min(llm_limits.llm_timeout_s(6.0), 20.0)
            try:
                conv = await asyncio.wait_for(asyncio.to_thread(hinglish.to_devanagari, text, timeout=conv_timeout),
                                              timeout=conv_timeout)
                spoken_text = conv["text"] or text
            except Exception as e:  # noqa: BLE001 (timeout included): the dictionary needs no model
                log.warning("Hinglish conversion failed, using the dictionary: %r", e)
                spoken_text = hinglish.dictionary_to_devanagari(text)
    if tts_language == "hi":
        # the Hindi voice splits "1,60,000" at its commas, skips "₹" and reads a phone number as an
        # amount: the digits are rewritten for speech only (services/speech_numbers.py)
        spoken_text = speech_numbers.hindi_numbers_for_speech(spoken_text)
    return spoken_text, tts_language


async def turn_voice(run: TurnRun, tts_language: str, fallback: bool) -> TtsPlan:
    """Route the turn to Gnani's Timbre: the agent's cloned voice (its speaker embedding) when it has one, else the preset
    voice of the language. A fallback reply always uses the preset voice. A saved voice whose embedding cannot be made (Gnani
    does not answer, the recording is gone) is replaced by the preset voice for this turn."""
    voice_id = run.agent.get("voice_id")
    clone_required = bool(voice_id) and not fallback
    embedding: Optional[List[float]] = None
    engine_key = engines.route_engine(tts_language, "fast", clone_required)
    if engine_key == gnani.TIMBRE_CLONE_ENGINE:
        engine_key, embedding, _name = await speaking.resolve_timbre_clone(tts_language, "fast", voice_id, run.user_id,
                                                                           run.storage_mode)
        if embedding is None:
            clone_required = False
            log.warning("turn: the voice %s has no Gnani embedding yet; replying with the preset voice", voice_id)
    return TtsPlan(engine_key, tts_language, clone_required, voice_id=voice_id, embedding=embedding,
                   phone=getattr(run, "source", None) == "phone")


async def turn_store(run: TurnRun, turn_id: str, audio_bytes: bytes, agent_text: str, *, engine_key: str,
                      llm_provider: Optional[str], llm_model: Optional[str], fallback: bool) -> Tuple[str, int]:
    """Save the (labelled) reply audio and write the conversation turns with their perf fields.
    Returns (audio URL, total latency in ms)."""
    local, sb = run.local, run.sb
    if not audio_bytes:   # a text-only reply (no Gnani key): there is no audio to keep
        audio_path, public_url = None, ""
    elif local:
        audio_path = f"local:turn_{turn_id}.wav"
        await asyncio.to_thread(lambda: open(os.path.join(state.LOCAL_AUDIO_DIR, f"turn_{turn_id}.wav"), "wb")
                                .write(audio_bytes))
        public_url = f"/api/conversations/turns/{turn_id}/audio"
    else:
        audio_path = f"{run.user_id}/{uuid.uuid4()}.wav"
        await storage.sb_upload(audio_path, audio_bytes, "audio/wav")
        public_url = await asyncio.to_thread(storage.signed_audio_url, sb, audio_path) or ""
    latency = int((time.time() - run.start_time) * 1000)
    conversation_id, user_text = run.conversation_id, run.user_text
    agent_text = call_events.redact_ids(agent_text)    # a reply that repeats an identity number is not kept with it
    # Per-turn performance, read back by GET /api/perf/turns (cloud: columns from migration 005_turn_metrics.sql;
    # first_audio_ms and llm_first_token_ms of streamed turns live inside the `timings` JSON column)
    perf_fields = {"timings": dict(run.timings), "total_ms": latency, "engine": engine_key,
                   "llm_provider": llm_provider, "llm_model": llm_model, "fallback": fallback,
                   "language": run.language}
    if run.tool_objs:
        perf_fields["tool_calls"] = tool_records(run)  # cloud: column from migration 009_agent_tools.sql
    retrieval_signals = getattr(run, "retrieval", None)
    if retrieval_signals:
        perf_fields["retrieval"] = retrieval_signals    # cloud: jsonb column from migration 012_turn_retrieval.sql
    run.stored = True   # from here the turn is as good as in the history: a phone call must not carry its words on
    try:
        if local:
            def persist():
                state.local_db.insert("conversation_turns", {"conversation_id": conversation_id, "role": "user",
                                                       "text_content": user_text})
                state.local_db.insert("conversation_turns", {
                    "id": turn_id, "conversation_id": conversation_id, "role": "agent",
                    "text_content": agent_text, "audio_storage_path": audio_path, "latency_ms": latency,
                    **perf_fields})
            await asyncio.to_thread(persist)
        else:
            user_row = {"conversation_id": conversation_id, "role": "user", "text_content": user_text}
            agent_row = {"conversation_id": conversation_id, "role": "agent", "text_content": agent_text,
                         "audio_storage_path": audio_path, "latency_ms": latency}
            try:
                await asyncio.to_thread(lambda: sb.table("conversation_turns")
                                        .insert([user_row, {**agent_row, **perf_fields}]).execute())
            except Exception as e:  # noqa: BLE001 - migration 005 / 009 / 012 may not be applied yet
                if retrieval_signals:  # keep the metrics when only the retrieval column (migration 012) is missing
                    try:
                        slim = {k: v for k, v in perf_fields.items() if k != "retrieval"}
                        await asyncio.to_thread(lambda: sb.table("conversation_turns")
                                                .insert([user_row, {**agent_row, **slim}]).execute())
                        log.warning("could not store retrieval signals (apply migration 012_turn_retrieval.sql): %s", e)
                        return public_url, latency
                    except Exception:  # noqa: BLE001
                        pass
                log.warning("could not store turn metrics (apply migration 005_turn_metrics.sql): %s", e)
                await asyncio.to_thread(lambda: sb.table("conversation_turns")
                                        .insert([user_row, agent_row]).execute())
    except Exception as e:  # noqa: BLE001
        log.warning("could not persist conversation turn: %s", e)
    return public_url, latency


TYPED_TURN_MAX_CHARS = 1000


async def turn_blocking(run: TurnRun, file_bytes: bytes, conversation_id: Optional[str]) -> dict:
    """The blocking turn of `run` (what the endpoint above returns). Used for typed questions too
    (`run.text_input`: no ASR); the reply audio is left in `run.reply_audio`."""
    user_id = run.user_id
    local = run.local
    timings = run.timings

    # 1. ASR
    t = await turn_asr(run, file_bytes)
    user_text = run.user_text

    # 2. Context: conversation history, then a clarifying question (run.canned) or RAG retrieval
    t = await turn_context(run, conversation_id, t)
    await turn_caller_memory(run)
    run.emit_caller()
    conversation_id, history, chunks = run.conversation_id, run.history, run.chunks
    hit = run.canned

    # 3. LLM (the hosted language model), grounded on retrieved context
    fallback = False
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    target_language = run.language
    if hit is not None:  # a clarifying question: no tools, no language model
        agent_text = hit.text
        timings["llm_ms"] = 0
        timings["canned_reply_id"] = hit.id
    else:
        tools_ms = int(await turn_tools(run) * 1000)
        messages = turn_messages(run)
        llm_timeout = llm_limits.llm_timeout_s(8.0)
        gated = grounding.gate_reply(run)  # CONFIDENCE_GATE: nothing retrieved answers this -> the callback line
        try:
            if gated is not None:
                agent_text = gated
                timings["guard"] = "low_confidence"
            elif run.tool_specs:
                tool_timeout = llm_limits.llm_timeout_s(8.0)
                tt = await asyncio.wait_for(asyncio.to_thread(tool_turn, run, messages, tool_timeout),
                                            timeout=tool_timeout * 3 + tools_mod.CALL_TIMEOUT_S * tools_mod.MAX_TOOL_CALLS)
                agent_text, llm_provider, llm_model = tt.text, tt.provider, tt.model
            else:
                completion = await asyncio.wait_for(asyncio.to_thread(
                    llm.chat, messages, temperature=llm.answer_temperature(), max_tokens=160, timeout=llm_timeout),
                    timeout=llm_timeout)
                agent_text = (completion.text or "").strip()
                llm_provider, llm_model = completion.provider, completion.model
            if not agent_text:
                raise ValueError("empty reply")
            if gated is None:
                agent_text = await asyncio.to_thread(enforce_language, run, messages, agent_text, llm_timeout)
                guard = grounding.guard_for_run(run, messages)  # ANSWER_GUARD: every number must be in the sources
                if guard is not None:
                    agent_text, tripped = guard.guard_text(agent_text, _guard_split)
                    if tripped:
                        timings["guard"] = "number"
        except Exception as e:  # noqa: BLE001 (includes timeout and LLMUnavailable)
            llm_provider = llm_model = None
            if llm.is_unconfigured(e):
                # No key: the fixed line in the caller's language (not the canned fallback), no request was made
                agent_text = llm.no_key_reply(run.language)
                timings["guard"] = "no_key"
            else:
                log.warning("LLM error/timeout, using fallback reply: %r", e)
                fallback = True
                agent_text = TURN_FALLBACK_TEXT
        t = run.mark("llm_ms", t)
        tools_ms += sum(c.ms for c in run.tool_objs)
        if tools_ms:
            timings["tools_ms"] = tools_ms
            timings["llm_ms"] = max(0, timings["llm_ms"] - tools_ms)
    # The agent says it is an AI at the start of a call: only the first agent reply, and before speech synthesis so
    # the disclosure is spoken (and stored) as part of the reply.
    if turn_discloses(run):
        agent_text = f"{agent_prompt.ai_disclosure(target_language)} {agent_text}"

    # 4. Speech synthesis via the router
    canned_cached: List[bool] = []
    plan: Optional[TtsPlan] = None
    if hit is not None:
        # The same words every time (with or without the disclosure): rendered once per voice, cached unlabelled
        # (services/canned_reply.cached_audio); the provenance step below labels this turn's copy.
        _, tts_language = await turn_spoken(target_language, agent_text, fallback, use_llm=False)
    else:
        spoken_text, tts_language = await turn_spoken(target_language, agent_text, fallback)
    no_audio = False   # a typed message with no Gnani key: the reply is text only
    try:
        try:
            if hit is not None:
                plan = await turn_voice(run, tts_language, fallback)

                async def render(plan=plan):
                    spoken, _lang = await turn_spoken(target_language, agent_text, fallback)
                    return await speaking.turn_synth(plan, spoken)
                wav, output_sr, was_cached = await canned_audio(hit, plan, agent_text, render)
                canned_cached.append(was_cached)
            else:
                plan = await turn_voice(run, tts_language, fallback)
                wav, output_sr = await speaking.turn_synth(plan, spoken_text)
        except AppError as e:
            if e.code != "PROVIDER_NOT_CONFIGURED" or run.text_input is None:
                raise
            no_audio = True    # "Needs a Gnani key": the typed turn still gets its text reply, with no audio
            plan = plan or TtsPlan(engines.route_engine(tts_language, "fast", False), tts_language, False)
            wav, output_sr = np.zeros(0, dtype=np.float32), 24000
            timings["audio"] = "needs_gnani_key"
        t = run.mark("tts_ms", t)

        # 5. Storage & DB
        turn_id = run.turn_id
        engine_key = plan.engine_key
        if no_audio:
            audio_bytes_out, receipt = b"", {}
        else:
            audio_bytes_out = synthesis.to_wav_bytes(wav, output_sr)
            # Provenance: label the reply and sign a receipt for it before it is stored or served.
            # Only a cloned voice that was actually used is named; a preset or fallback reply has no consent record.
            used_voice = plan.used_voice
            consent_id, consent_verified, voice_name = await voice_store.voice_consent(used_voice, run.storage_mode, user_id)
            audio_bytes_out, receipt = await asyncio.to_thread(
                lambda: provenance.apply_provenance(
                    kind="agent_reply", subject_id=turn_id, wav_bytes=audio_bytes_out, text=agent_text,
                    engine=engine_key, voice_id=used_voice, voice_name=voice_name, consent_id=consent_id,
                    consent_verified=consent_verified, user_id=user_id, cloud=not local))
        run.reply_audio = audio_bytes_out
        public_url, latency = await turn_store(run, turn_id, audio_bytes_out, agent_text, engine_key=engine_key,
                                                llm_provider=llm_provider, llm_model=llm_model, fallback=fallback)
        turn_memory_commit(run)
        turn_commit_utterance(run, turn_id)
        run.emit_agent(0, agent_text, int(len(wav) / max(1, output_sr) * 1000) if hasattr(wav, "__len__") else None)
        await turn_link_bookings(run, receipt.get("receipt_id"))
        run.emit("turn_end", language=run.language, tool_calls=len(run.tool_objs), fallback=fallback)

        return {
            "user_text": user_text,
            "agent_text": agent_text,
            "agent_audio_url": public_url or None,
            "latency_ms": latency,
            "total_ms": latency,
            "timings": timings,
            "engine": engine_key,
            "engine_name": engines.ENGINE_REGISTRY[engine_key]["name"],
            "route_reason": "LLM fallback reply" if fallback else engines.ENGINE_ROUTE_REASON.get(engine_key, ""),
            "grounded": bool(chunks),
            "sources_used": len(chunks),
            "retrieval": run.retrieval,
            "fallback": fallback,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "language": target_language,
            "language_check": run.language_check,
            "conversation_id": conversation_id,
            "receipt_id": receipt.get("receipt_id"),
            "tool_calls": tool_records(run),
            **({"canned_reply": canned_info(hit, canned_cached)} if hit is not None else {}),
        }
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("Turn error")
        raise AppError("TURN_FAILED", "Failed to process conversation turn", 500, {"reason": str(e)[:200]})
