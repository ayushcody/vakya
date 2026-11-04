"""The streaming turn: the reply is spoken sentence by sentence while the model is still writing it, with the filler clip when the
answer is slow, the disclosure and filler caches, and the short-lived sentence clips the app plays."""

import asyncio
import base64
import collections
import concurrent.futures
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from core import engines, state
    from core.errors import AppError
    from services import (
        agent_prompt, call_events, gnani, grounding, hinglish, language_policy, llm, llm_limits, provenance,
        sentences as sentences_mod, speaking, synthesis, tools as tools_mod, turn_pipeline, voice_store,
    )
    from services.speaking import TtsPlan
    from services.turn_pipeline import TurnRun
except ImportError:  # pragma: no cover
    from backend.core import engines, state
    from backend.core.errors import AppError
    from backend.services import (
        agent_prompt, call_events, gnani, grounding, hinglish, language_policy, llm, llm_limits, provenance,
        sentences as sentences_mod, speaking, synthesis, tools as tools_mod, turn_pipeline, voice_store,
    )
    from backend.services.speaking import TtsPlan
    from backend.services.turn_pipeline import TurnRun

log = logging.getLogger("vakya")

SENTENCE_GAP_S = 0.08  # silence between sentences in the stitched full reply
HINGLISH_GRACE_S = 0.5  # how long the speech task waits for a background Devanagari rendering of a later sentence
_STREAM_TURN_OWNERS: "collections.OrderedDict[str, str]" = collections.OrderedDict()  # turn id -> user id
_DISCLOSURE_CLIPS: Dict[tuple, Tuple[np.ndarray, int]] = {}  # (engine, language, voice, text) -> audio


# Filler clips ("One moment."): when the answer is slow, a short clip plays while the model is still working. Timbre
# renders each once per language (the voice follows the language) and the audio is kept in memory. Hinglish is spoken by
# the Hindi voices, so it uses the Hindi fillers.
FILLER_TEXTS: Dict[str, List[str]] = {
    "en": ["One moment.", "Let me check."],
    "hi": ["एक सेकंड।", "देखती हूँ।"],
}


CLIP_CACHE_MAX = 64
_FILLER_CLIPS: Dict[tuple, Tuple[np.ndarray, int]] = {}  # (engine, tts language, text) -> audio
FILLER_PREPARE_S = 0.12  # how long before its deadline the filler's audio is prepared (the WAV takes ~30 ms)
_FILLER_LAST: "collections.OrderedDict[str, int]" = collections.OrderedDict()  # conversation id -> last filler index


def _filler_enabled() -> bool:
    return os.getenv("FILLER_AUDIO", "on").strip().lower() not in ("off", "0", "false", "no")


def filler_after_s(source: Optional[str] = None) -> float:
    """How long after the transcript the answer may take before a filler plays: FILLER_AFTER_MS (default 900), or for a
    phone call (`source` "phone") PHONE_FILLER_AFTER_MS (default 500): a caller on the line hears silence as a dropped
    call far sooner than a person at a screen does."""
    name, default = ("PHONE_FILLER_AFTER_MS", "500") if source == "phone" else ("FILLER_AFTER_MS", "900")
    try:
        return max(0.0, float(os.getenv(name, default))) / 1000.0
    except ValueError:
        return float(default) / 1000.0


def pick_filler(language: str, conversation_id: Optional[str]) -> Optional[str]:
    """The next filler line of a conversation (round robin, so it never repeats back to back), or None when the
    reply language has none."""
    texts = FILLER_TEXTS.get("hi" if language in ("hi", "hinglish") else language)
    if not texts:
        return None
    key = conversation_id or ""
    nxt = (_FILLER_LAST.get(key, -1) + 1) % len(texts)
    if key:
        _FILLER_LAST[key] = nxt
        _FILLER_LAST.move_to_end(key)
        while len(_FILLER_LAST) > 512:
            _FILLER_LAST.popitem(last=False)
    return texts[nxt]


async def prerender_turn_clips(languages: Tuple[str, ...] = ("en", "hi", "hinglish")) -> int:
    """Render the filler clips and the AI-disclosure clip of each language with Timbre's preset voice into _FILLER_CLIPS and
    _DISCLOSURE_CLIPS, under the keys a live turn looks them up by, so the first call after a restart hears them at once
    instead of waiting for Timbre. Spends a few Gnani requests (the gateway caches what it has already rendered) and does
    nothing without a Gnani key. Returns how many clips were rendered now."""
    if not gnani.configured():
        return 0
    rendered = 0
    for language in languages:
        for text in [*FILLER_TEXTS.get("hi" if language in ("hi", "hinglish") else language, []),
                     agent_prompt.ai_disclosure(language)]:
            is_disclosure = text == agent_prompt.ai_disclosure(language)
            spoken, tts_language = await turn_pipeline.turn_spoken(language, text, False, use_llm=False)
            key = (gnani.TIMBRE_ENGINE, tts_language, None, spoken) if is_disclosure else (gnani.TIMBRE_ENGINE, tts_language, spoken)
            cache = _DISCLOSURE_CLIPS if is_disclosure else _FILLER_CLIPS
            if key in cache:
                continue
            wav, sr = await speaking.turn_synth(TtsPlan(gnani.TIMBRE_ENGINE, tts_language, False), spoken)
            cache[key] = (synthesis.to_numpy(wav), sr)
            rendered += 1
    return rendered


_CLIP_NAME_RE = re.compile(r"^turn_[0-9a-f-]{36}_\d+\.wav$")
CLIP_TTL_S = 3600


def error_data(e: AppError) -> dict:
    """The body of an `error` event: the code and message, and the plan the Gnani gateway attached (`details.degraded`,
    `action`, `hold_line`, `retry_after_s`) so a live call can play its held line."""
    details = e.details if isinstance(e.details, dict) else {}
    plan = {k: details[k] for k in ("degraded", "action", "hold_line", "retry_after_s", "needs_key") if k in details}
    return {"code": e.code, "message": e.message, **({"details": plan} if plan else {})}


def sse_event(event: str, data: dict) -> bytes:
    """One server-sent event: `event: name` and a single JSON `data:` line."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def remember_stream_turn(turn_id: str, user_id: str) -> None:
    _STREAM_TURN_OWNERS[turn_id] = user_id
    while len(_STREAM_TURN_OWNERS) > 512:
        _STREAM_TURN_OWNERS.popitem(last=False)


def prune_sentence_clips() -> None:
    """Sentence clips only matter while the reply plays; the stitched reply is what is kept."""
    cutoff = time.time() - CLIP_TTL_S
    try:
        for name in os.listdir(state.LOCAL_AUDIO_DIR):
            if _CLIP_NAME_RE.match(name):
                path = os.path.join(state.LOCAL_AUDIO_DIR, name)
                if os.path.getmtime(path) < cutoff:
                    os.unlink(path)
    except OSError:
        pass


async def turn_event_stream(run: TurnRun, file_bytes: bytes, conversation_id: Optional[str], turn_id: str):
    """The streaming turn as server-sent events: transcript, sentence (one per spoken sentence), then done or error.

    The language model streams text in a worker thread; completed sentences go to a speech task that synthesizes and
    emits them in order while the model is still writing. The AI disclosure is its own first sentence and is spoken
    while the model is still starting up."""
    loop = asyncio.get_running_loop()
    run.turn_id = turn_id   # the live call log (services/call_events.py) names this exchange by it
    out: asyncio.Queue = asyncio.Queue()        # events for the client; None ends the stream
    sentences: asyncio.Queue = asyncio.Queue()  # LLM thread -> speech task
    cancel = threading.Event()
    tasks: List[asyncio.Task] = []
    local = run.local
    # Shared by the speech task and the filler: the next sentence index, whether an answer sentence (or the AI
    # disclosure) is already on its way, whether the turn has ended, whether the disclosure is spoken (None: unknown
    # yet), when the filler was sent, and whether the answer has begun (the model's first words are out, or a sentence
    # of it is being spoken): then the audio is moments away and a filler would only delay it.
    fstate: Dict[str, Any] = {"index": 0, "claimed": False, "closed": False, "discloses": None, "audio_ms": None,
                              "answer_started": False}

    async def filler_watch(deadline: float) -> None:
        """When no sentence of the reply has been emitted by `deadline` (FILLER_AFTER_MS after the transcript, or
        PHONE_FILLER_AFTER_MS on a call), send one short filler clip, as a `sentence` event with `filler: true`. It is
        not part of the reply: it stays out of the reply text, the stored transcript and the stitched audio. Never when
        the AI disclosure is spoken (it fills the wait), never twice, never once a sentence of the reply is emitted or
        being emitted, and never once the model has started answering: its first clause would be spoken about as soon
        as a filler could be (a filler is a second or so long, so it would delay the answer, not cover for it).

        The clip is prepared a little ahead of the deadline (voice plan, WAV), so it leaves at the deadline
        instead of one preparation later; the guards are checked again after that, with no await before the event."""
        try:
            lead = min(FILLER_PREPARE_S, max(0.0, deadline - time.time() - 0.05))
            await asyncio.sleep(max(0.0, deadline - lead - time.time()))
            if not gnani.configured():
                return   # no Gnani key: no voice to speak a filler with
            if fstate["claimed"] or fstate["closed"] or fstate["answer_started"]:
                return
            discloses_now = fstate["discloses"]
            if discloses_now is None:  # the history is not loaded yet: a new conversation discloses
                discloses_now = run.agent.get("disclose_ai") is not False and not conversation_id
            if discloses_now or run.agent.get("voice_id"):
                return  # (a cloned voice: its turns get no filler)
            text = pick_filler(run.language, conversation_id or run.conversation_id)
            if text is None:
                return
            spoken, tts_language = await turn_pipeline.turn_spoken(run.language, text, False, use_llm=False)
            plan = await turn_pipeline.turn_voice(run, tts_language, True)  # the fast preset path, like the canned fallback line
            key = (plan.engine_key, plan.tts_language, spoken)
            cached = _FILLER_CLIPS.get(key)
            if cached is None:
                wav, sr = await speaking.turn_synth(plan, spoken)
                cached = _FILLER_CLIPS[key] = (synthesis.to_numpy(wav), sr)
                while len(_FILLER_CLIPS) > CLIP_CACHE_MAX:   # a cache keyed by voice and text must not grow for ever
                    _FILLER_CLIPS.pop(next(iter(_FILLER_CLIPS)))
            wav, sr = cached
            data = await asyncio.to_thread(synthesis.to_wav_bytes, wav, sr)
            await asyncio.sleep(max(0.0, deadline - time.time()))
            if fstate["claimed"] or fstate["closed"] or fstate["answer_started"]:
                return  # the answer got there first: no awaits from here to the event, so the order holds
            index = fstate["index"]
            fstate["index"] += 1
            if local:
                with open(os.path.join(state.LOCAL_AUDIO_DIR, f"turn_{turn_id}_{index}.wav"), "wb") as f:
                    f.write(data)
                url = f"/api/conversations/turns/{turn_id}/sentences/{index}/audio"
            else:
                url = "data:audio/wav;base64," + base64.b64encode(data).decode("ascii")
            fstate["audio_ms"] = int((time.time() - run.start_time) * 1000)
            out.put_nowait(sse_event("sentence", {"index": index, "text": text, "audio_url": url, "filler": True}))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 (a filler is never worth failing the turn)
            log.warning("filler skipped: %r", e)

    try:
        tools_task: Optional["asyncio.Future"] = None
        if run.agent.get("tools"):
            # Which tools the agent's MCP servers offer depends on the agent only, not on what the caller said: find out
            # while the recording is being recognised (and, on the first call, while a tool server starts).
            tools_task = asyncio.ensure_future(turn_pipeline.turn_tools(run))
            tasks.append(tools_task)
        t = await turn_pipeline.turn_asr(run, file_bytes)
        yield sse_event("transcript", {"user_text": run.user_text, "language": run.language})
        if _filler_enabled():
            tasks.append(asyncio.create_task(filler_watch(time.time() + filler_after_s(run.source))))

        async def setup() -> int:
            await turn_pipeline.turn_context(run, conversation_id, t)
            await turn_pipeline.turn_caller_memory(run)
            run.emit_caller()
            fstate["discloses"] = turn_pipeline.turn_discloses(run)
            if run.canned is not None:
                fstate["answer_started"] = True  # the answer is ready now: no filler
                return 0                         # (a canned reply is never a tool's work: the tools are not awaited)
            if tools_task is None:
                return 0
            waited = time.time()
            await tools_task
            return int((time.time() - waited) * 1000)  # only the wait that the tools added counts as tools time

        # Conversation, retrieval and tools run in a task, so a filler can reach the caller while they are slow.
        setup_task = asyncio.ensure_future(setup())
        tasks.append(setup_task)
        while not setup_task.done():
            getter = asyncio.ensure_future(out.get())
            try:
                await asyncio.wait({setup_task, getter}, return_when=asyncio.FIRST_COMPLETED)
            except BaseException:
                getter.cancel()
                raise
            if getter.done():
                yield getter.result()
            else:
                getter.cancel()
        discovery_ms = setup_task.result()
        messages = turn_pipeline.turn_messages(run)
        discloses = turn_pipeline.turn_discloses(run)
        llm_timeout = llm_limits.llm_timeout_s(8.0)
        # On a phone call the wait for the model's next bytes is capped harder (5 s) so a stalled model is given up on
        # before the caller does; the whole reply keeps the usual budget.
        stall_timeout = min(llm_timeout, llm_limits.phone_stall_timeout()) if run.source == "phone" else llm_timeout
        llm_started = time.time()
        llm_state: Dict[str, Any] = {"done": False, "first_token_ms": None, "ms": None, "provider_holds_lock": False}

        conv_futures: Dict[str, "concurrent.futures.Future"] = {}   # sentence -> background Devanagari rendering
        conv_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="hinglish")
        known_text = " ".join(h["text_content"] for h in run.history) + " " + run.user_text

        def llm_worker() -> None:
            """Blocking: stream the model, hand finished sentences to the speech task.

            An agent with tools first decides non-streamed in effect (a short JSON request is read in full, the tool runs),
            then its answer streams like any other. Every sentence of such an answer is checked for a booking id no tool
            returned before it is handed on (services/tools.py guard_sentence): a failing one is replaced by the safe
            line and the reply stops there."""
            def put(item) -> None:
                try:
                    loop.call_soon_threadsafe(sentences.put_nowait, item)
                except RuntimeError:  # the loop is gone (client left)
                    cancel.set()
            splitter = sentences_mod.turn_splitter()
            stream = None
            stopped = False
            sent = 0
            answer_guard = grounding.guard_for_run(run, messages)  # ANSWER_GUARD (None when off)

            def emit(s: str) -> None:
                """Hand one finished sentence to the speech task (through the booking-id guard for tool agents, then
                the number guard: a sentence naming a number, time, fee or phone number that nothing the model was given
                contains is replaced by the callback line, and the reply stops there)."""
                nonlocal stopped, sent
                if stopped:
                    return
                if run.tool_specs:
                    safe = tools_mod.guard_sentence(s, run.tool_objs, known_text)
                    if safe:
                        put(("sentence", safe))
                        stopped = True
                        return
                if answer_guard is not None:
                    line = answer_guard.replacement(s, spoken_before=sent > 0)
                    if line is not None:
                        log.warning("reply named a value not in the sources (%s); replaced: %r",
                                    ", ".join(v.text for v in answer_guard.tripped or []), s)
                        run.timings["guard"] = "number"
                        put(("sentence", line))
                        stopped = True
                        return
                if (run.language == "hinglish" and sent >= 1 and not llm_state["provider_holds_lock"]
                        and hinglish.is_roman_script(s) and s not in conv_futures):
                    # Start the model's Devanagari rendering now: it runs while the previous sentence is spoken.
                    conv_futures[s] = conv_pool.submit(hinglish.to_devanagari, s, timeout=HINGLISH_GRACE_S * 4)
                sent += 1
                put(("sentence", s))

            try:
                gated = grounding.gate_reply(run)  # CONFIDENCE_GATE: nothing retrieved answers this
                if gated is not None:
                    run.timings["guard"] = "low_confidence"
                    llm_state["first_token_ms"] = 0
                    fstate["answer_started"] = True
                    emit(gated)
                    llm_state["ms"] = 0
                    llm_state["done"] = True
                    put(("llm_done", llm.StreamResult(gated, None, None, 0, {}, 0)))
                    return
                if run.tool_specs and os.getenv("VAKYA_TOOL_STREAM", "on").strip().lower() in ("off", "0", "false"):
                    # The switch back to the blocking tool turn: the whole answer is written before it is spoken.
                    tt = turn_pipeline.tool_turn(run, messages, llm_timeout)
                    if not tt.text:
                        raise ValueError("empty reply")
                    tt.text = turn_pipeline.enforce_language(run, messages, tt.text, llm_timeout)
                    llm_state["first_token_ms"] = int((time.time() - llm_started) * 1000)
                    fstate["answer_started"] = True
                    for s in [*splitter.feed(tt.text), *splitter.flush()]:
                        emit(s)
                    llm_state["ms"] = llm_state["first_token_ms"]
                    llm_state["done"] = True
                    put(("llm_done", llm.StreamResult(tt.text, tt.provider or "", tt.model or "", llm_state["ms"], {},
                                                      llm_state["first_token_ms"])))
                    return
                if run.tool_specs:
                    previous = next((h["text_content"] for h in reversed(run.history) if h["role"] == "agent"),
                                    None) if run.history_known else None
                    stream = tools_mod.ToolStream(messages, run.tool_specs, previous_agent_text=previous,
                                                  known_text=known_text, timeout=stall_timeout,
                                                  total_timeout=llm_timeout, calls=run.tool_objs,
                                                  known_slots=tools_mod.known_slots(run.history),
                                                  caller_text=run.user_text)
                else:
                    stream = llm.chat_stream(messages, temperature=llm.answer_temperature(), max_tokens=160,
                                             timeout=stall_timeout, total_timeout=llm_timeout)
                # The first sentence is checked against the reply language. When it is in the wrong one, nothing is
                # spoken yet: the whole reply is held, checked again and regenerated once (language_policy.enforce).
                held: List[str] = []
                first_checked = wrong_language = False

                def route(s: str) -> None:
                    nonlocal first_checked, wrong_language
                    if not first_checked and len(s.split()) >= 3:
                        first_checked = True
                        wrong_language = not language_policy.reply_matches(run.language, s,
                                                                           turn_pipeline.language_known_text(messages))
                        if not wrong_language:
                            run.language_check = {"target": run.language, "matched": True, "retried": False,
                                                  "retry_matched": None}
                    if wrong_language:
                        held.append(s)
                    else:
                        emit(s)

                for delta in stream:
                    if llm_state["first_token_ms"] is None:
                        llm_state["first_token_ms"] = int((time.time() - llm_started) * 1000)
                        fstate["answer_started"] = True  # the first words are out: no filler from here on
                    llm_state["provider_holds_lock"] = bool(stream.info.get("lock_held"))
                    if cancel.is_set():
                        return
                    for s in splitter.feed(delta):
                        route(s)
                    if stopped:
                        break
                if not stopped:
                    for s in splitter.flush():
                        route(s)
                if wrong_language and not stopped:
                    fixed = turn_pipeline.enforce_language(run, messages, " ".join(held), llm_timeout)
                    again = sentences_mod.turn_splitter(first_phrase=False)
                    for s in [*again.feed(fixed), *again.flush()]:
                        emit(s)
                llm_state["ms"] = int((time.time() - llm_started) * 1000)
                llm_state["done"] = True
                result = stream.result or llm.StreamResult("", getattr(stream, "provider", None) or "",
                                                           getattr(stream, "model", None) or "", llm_state["ms"], {},
                                                           llm_state["first_token_ms"])
                put(("llm_done", result))
            except llm.LLMStreamInterrupted as e:  # stop and report: whatever was complete has been spoken
                llm_state["ms"] = int((time.time() - llm_started) * 1000)
                llm_state["done"] = True
                put(("llm_interrupted", e))
            except Exception as e:  # noqa: BLE001 (LLMUnavailable, timeout, a provider that failed to connect)
                llm_state["ms"] = int((time.time() - llm_started) * 1000)
                llm_state["done"] = True
                if llm.is_unconfigured(e) and sent == 0 and not stopped:
                    # No key: the fixed line in the caller's language is the reply (nothing was requested)
                    line = llm.no_key_reply(run.language)
                    run.timings["guard"] = "no_key"
                    llm_state["first_token_ms"] = 0
                    fstate["answer_started"] = True
                    emit(line)
                    put(("llm_done", llm.StreamResult(line, llm.NO_KEY_PROVIDER, "", 0, {}, 0)))
                else:
                    put(("llm_failed", e))
            finally:
                if stream is not None:
                    stream.close()

        async def speech_task() -> None:
            parts: List[str] = []         # the sentences of the reply, as the agent wrote them
            clips: List[np.ndarray] = []  # the audio of each spoken sentence, at one sample rate
            clip_sr = 24000
            tts_ms = 0
            first_audio_ms: Optional[int] = None
            first_answer_audio_ms: Optional[int] = None
            text_only = {"on": False}      # a typed message with no Gnani key: no audio at all
            main_plan: Optional[TtsPlan] = None
            fb_plan: Optional[TtsPlan] = None
            fallback = False
            interrupted: Optional[BaseException] = None
            summary = None
            canned_cached: List[bool] = []  # per sentence of a canned reply: its audio came from the cache

            async def speak(text: str, *, disclosure: bool = False, as_fallback: bool = False) -> None:
                nonlocal tts_ms, first_audio_ms, first_answer_audio_ms, main_plan, fb_plan, clip_sr, canned_cached
                started = time.time()
                # Hinglish is converted to Devanagari for the voice, and no model call may sit in front of the first
                # audio: the sentence is converted with the dictionary at once. A later sentence of a reply may have a
                # model rendering that was started when it was written (conv_futures); it is used when it is ready
                # within a short grace (the previous sentence is still playing), else the dictionary reading stands.
                converted: Optional[str] = None
                fut = conv_futures.pop(text, None)
                if fut is not None and not disclosure and not as_fallback:
                    try:
                        converted = ((await asyncio.wait_for(asyncio.wrap_future(fut), HINGLISH_GRACE_S))
                                     .get("text") or None)
                    except Exception:  # noqa: BLE001 (timeout, model error): the dictionary reading stands
                        converted = None
                spoken, tts_language = await turn_pipeline.turn_spoken(run.language, text, as_fallback, use_llm=False,
                                                          converted=converted)
                if as_fallback:
                    if fb_plan is None:
                        fb_plan = await turn_pipeline.turn_voice(run, tts_language, True)
                    plan = fb_plan
                else:
                    if main_plan is None:
                        main_plan = await turn_pipeline.turn_voice(run, tts_language, False)
                    plan = main_plan
                # The AI disclosure is the same words every call: synthesize it once per voice and language, so the
                # caller hears it as soon as speech recognition is done (it is labelled afresh each turn).
                cache_key = (plan.engine_key, plan.tts_language, plan.voice_id if plan.clone_required else None,
                             spoken)
                cached = _DISCLOSURE_CLIPS.get(cache_key) if disclosure else None
                if cached is not None:
                    wav, sr = cached
                elif run.canned is not None and not disclosure and not as_fallback:
                    # A canned reply is the same words every time: its sentences are rendered once per voice
                    # (services/canned_reply.cached_audio) and used as it is below, like every other clip.
                    async def render(plan=plan, spoken=spoken):
                        return await speaking.turn_synth(plan, spoken)
                    wav, sr, was_cached = await turn_pipeline.canned_audio(run.canned, plan, text, render)
                    canned_cached.append(was_cached)
                else:
                    try:
                        wav, sr = await speaking.turn_synth(plan, spoken)
                    except AppError as e:
                        if e.code != "PROVIDER_NOT_CONFIGURED" or run.text_input is None:
                            raise
                        # A typed message with no Gnani key: the reply is text only (no audio), the sentence still goes out.
                        run.timings["audio"] = "needs_gnani_key"
                        text_only["on"] = True
                        index = fstate["index"]
                        fstate["index"] += 1
                        fstate["claimed"] = True
                        parts.append(text)
                        out.put_nowait(sse_event("sentence", {"index": index, "text": text, "audio_url": None}))
                        run.emit_agent(index, text, None, disclosure=disclosure)
                        return
                    wav = synthesis.to_numpy(wav)
                    if disclosure:
                        _DISCLOSURE_CLIPS[cache_key] = (wav, sr)
                        while len(_DISCLOSURE_CLIPS) > 32:
                            _DISCLOSURE_CLIPS.pop(next(iter(_DISCLOSURE_CLIPS)))
                data = await asyncio.to_thread(synthesis.to_wav_bytes, wav, sr)
                index = fstate["index"]  # from here on no filler may be sent: this sentence is next
                fstate["index"] += 1
                fstate["claimed"] = True
                if local:
                    path = os.path.join(state.LOCAL_AUDIO_DIR, f"turn_{turn_id}_{index}.wav")
                    await asyncio.to_thread(lambda: open(path, "wb").write(data))
                    url = f"/api/conversations/turns/{turn_id}/sentences/{index}/audio"
                else:
                    url = "data:audio/wav;base64," + base64.b64encode(data).decode("ascii")
                tts_ms += int((time.time() - started) * 1000)
                now_ms = int((time.time() - run.start_time) * 1000)
                if first_audio_ms is None:
                    first_audio_ms = now_ms
                if first_answer_audio_ms is None and not disclosure:
                    first_answer_audio_ms = now_ms
                parts.append(text)
                if not clips:
                    clip_sr = sr
                clips.append(synthesis.resample_linear(synthesis.to_numpy(wav), sr, clip_sr))  # the rates of one turn may differ
                out.put_nowait(sse_event("sentence", {"index": index, "text": text, "audio_url": url}))
                run.emit_agent(index, text, int(len(wav) / max(1, sr) * 1000), disclosure=disclosure)

            try:
                if discloses:
                    await speak(agent_prompt.ai_disclosure(run.language), disclosure=True)
                spoke_answer = False
                while True:
                    kind, payload = await sentences.get()
                    if kind == "sentence":
                        await speak(payload)
                        spoke_answer = True
                    elif kind == "llm_done" and spoke_answer:
                        summary = payload
                        break
                    elif kind == "llm_interrupted" and spoke_answer:
                        interrupted = payload
                        summary = llm.StreamResult(payload.partial, payload.provider, payload.model,
                                                   llm_state["ms"] or 0, {}, llm_state["first_token_ms"])
                        break
                    else:  # llm_failed, an empty reply, or interrupted before a complete sentence: the canned line
                        log.warning("LLM error/timeout, using fallback reply: %r", payload)
                        fallback = True
                        await speak(turn_pipeline.TURN_FALLBACK_TEXT, as_fallback=True)
                        break

                if fstate["audio_ms"] is not None and (first_audio_ms is None or fstate["audio_ms"] < first_audio_ms):
                    first_audio_ms = fstate["audio_ms"]  # a filler was the first thing the caller could hear
                # A speculative phone turn (routers/phone.py sets run.commit_gate) may have finished the model
                # and every sentence before the caller's end of speech is confirmed: it persists nothing (receipt,
                # stored turn, webhook) until it is. A cancelled one is cancelled right here and leaves nothing behind.
                # Every other turn has no gate and goes straight on.
                gate = getattr(run, "commit_gate", None)
                if gate is not None and not gate.is_set():
                    waited = time.time()
                    await gate.wait()
                    run.timings["commit_wait_ms"] = int((time.time() - waited) * 1000)  # the head start it had
                # The stitched full reply: every sentence, labelled as a whole, with its receipt.
                gap = np.zeros(int(clip_sr * SENTENCE_GAP_S), dtype=np.float32)
                pieces: List[np.ndarray] = []
                for i, clip in enumerate(clips):
                    if i:
                        pieces.append(gap)
                    pieces.append(clip)
                full = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
                agent_text = " ".join(parts)
                final_plan = fb_plan if fallback and fb_plan else main_plan
                engine_key = final_plan.engine_key if final_plan else gnani.TIMBRE_ENGINE
                used_voice = (main_plan.used_voice if main_plan and not fallback else None)
                consent_id, consent_verified, voice_name = await voice_store.voice_consent(used_voice, run.storage_mode,
                                                                                run.user_id)
                if text_only["on"] or not clips:
                    audio_out, receipt = b"", {}
                else:
                    full_wav = await asyncio.to_thread(synthesis.to_wav_bytes, full, clip_sr)
                    audio_out, receipt = await asyncio.to_thread(
                        lambda: provenance.apply_provenance(
                            kind="agent_reply", subject_id=turn_id, wav_bytes=full_wav, text=agent_text,
                            engine=engine_key, voice_id=used_voice, voice_name=voice_name, consent_id=consent_id,
                            consent_verified=consent_verified, user_id=run.user_id, cloud=not local))
                run.timings.update({
                    "llm_first_token_ms": llm_state["first_token_ms"] if llm_state["first_token_ms"] is not None
                    else (llm_state["ms"] or 0),
                    "llm_ms": llm_state["ms"] or 0, "tts_ms": tts_ms, "first_audio_ms": first_audio_ms or 0})
                tools_ms = discovery_ms + sum(c.ms for c in run.tool_objs)
                if tools_ms:  # the tool calls ran inside the model's time: report them apart from the model's own
                    run.timings["tools_ms"] = tools_ms
                    for key in ("llm_ms", "llm_first_token_ms"):
                        run.timings[key] = max(0, run.timings[key] - sum(c.ms for c in run.tool_objs))
                if first_answer_audio_ms is not None and first_answer_audio_ms != first_audio_ms:
                    run.timings["first_answer_audio_ms"] = first_answer_audio_ms
                if run.canned is not None:
                    run.timings["canned_reply_id"] = run.canned.id
                provider = None if fallback else (summary.provider if summary else None)
                model = None if fallback else (summary.model if summary else None)
                public_url, latency = await turn_pipeline.turn_store(
                    run, turn_id, audio_out, agent_text, engine_key=engine_key, llm_provider=provider,
                    llm_model=model, fallback=fallback)
                turn_pipeline.turn_memory_commit(run)
                turn_pipeline.turn_commit_utterance(run, turn_id)
                await turn_pipeline.turn_link_bookings(run, receipt.get("receipt_id"))
                run.emit("turn_end", language=run.language, tool_calls=len(run.tool_objs), fallback=fallback,
                         interrupted=interrupted is not None)
                out.put_nowait(sse_event("done", {
                    "turn_id": turn_id,
                    "user_text": run.user_text,
                    "agent_text": agent_text,
                    "agent_audio_url": public_url or None,
                    "timings": {**run.timings, "total_ms": latency},
                    "total_ms": latency,
                    "engine": engine_key,
                    "engine_name": engines.ENGINE_REGISTRY[engine_key]["name"],
                    "route_reason": "LLM fallback reply" if fallback else engines.ENGINE_ROUTE_REASON.get(engine_key, ""),
                    "grounded": bool(run.chunks),
                    "sources_used": len(run.chunks),
                    "retrieval": run.retrieval,
                    "llm_provider": provider,
                    "llm_model": model,
                    "language": run.language,
                    "language_check": run.language_check,
                    "fallback": fallback,
                    "interrupted": interrupted is not None,
                    "receipt_id": receipt.get("receipt_id"),
                    "conversation_id": run.conversation_id,
                    "tool_calls": turn_pipeline.tool_records(run),
                    **({"canned_reply": turn_pipeline.canned_info(run.canned, canned_cached)} if run.canned is not None else {}),
                }))
            except AppError as e:
                run.emit("turn_end", error=e.code)
                out.put_nowait(sse_event("error", error_data(e)))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("Streaming turn error")
                run.emit("turn_end", error="TURN_FAILED")
                out.put_nowait(sse_event("error", {"code": "TURN_FAILED", "message": "Failed to process conversation "
                                                                                  "turn"}))
            finally:
                fstate["closed"] = True
                out.put_nowait(None)

        tasks.append(asyncio.create_task(speech_task()))
        if run.canned is not None:
            # A clarifying question: its sentences go to the speech task as the model's would, and no model runs.
            splitter = sentences_mod.turn_splitter()
            for piece in [*splitter.feed(run.canned.text), *splitter.flush()]:
                sentences.put_nowait(("sentence", piece))
            llm_state.update(done=True, first_token_ms=0, ms=0)
            sentences.put_nowait(("llm_done", llm.StreamResult(run.canned.text, None, None, 0, {}, 0)))
            llm_future = None
        else:
            llm_future = loop.run_in_executor(None, llm_worker)
        while True:
            chunk = await out.get()
            if chunk is None:
                break
            yield chunk
        del llm_future  # the worker handles its own errors and ends on its own
    except AppError as e:
        yield sse_event("error", error_data(e))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.exception("Streaming turn error")
        yield sse_event("error", {"code": "TURN_FAILED", "message": "Failed to process conversation turn"})
    finally:
        cancel.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        pool = locals().get("conv_pool")
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
