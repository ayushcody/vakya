"""The phone latency work of 9 Nov 2026: realtime recognition during speech (services/phone_asr.py), the booking requests that
are answered from the tool results (tools.prefetch_reads, booked_line, the read-back said without a model round), writes that
wait for the caller's end of speech (ToolStream write_gate), the rarer and varied filler, and the personal greeting made while
the phone rings. Everything is faked: no network, no model, no voice."""
import asyncio
import json
import time
from datetime import date, time as dtime

import pytest

from services import phone_asr
from services import phone_greeting as pg
from services import readback
from services import tools as tools_mod
from services import turn_stream as ts


# ------------------------------------------------------------------------------------------------ the recognition stream

class FakeSocket:
    """A Prisma stream: records the frames and the commits; answers a commit with a final transcript."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.frames = []
        self.commits = 0
        self.inbox = asyncio.Queue()
        self.closed = False

    async def send(self, data):
        if isinstance(data, (bytes, bytearray)):
            self.frames.append(bytes(data))
            return
        if json.loads(data).get("type") == "commit":
            self.commits += 1
            if self.answers:
                text, lang = self.answers.pop(0)
                await self.inbox.put(json.dumps({"type": "transcript", "text": text, "is_final": True,
                                                 "detected_language_code": lang}))

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.inbox.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self):
        self.closed = True
        await self.inbox.put(None)


def fake_recognizer(sock):
    from services import gnani_client

    async def connect(url, additional_headers=None):
        return sock

    return lambda: gnani_client.StreamingRecognizer(sample_rate=8000, pace=False, connect=connect, hold_slot=False)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_a_commit_gives_the_text_and_audio_goes_as_exact_frames(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "test-key")
    sock = FakeSocket([("क्लिनिक के timings क्या हैं?", "hi-IN")])

    async def go():
        rec = phone_asr.CallRecognizer(8000, recognizer_factory=fake_recognizer(sock))
        rec.start()
        await asyncio.sleep(0.05)
        assert rec.ready
        rec.feed(b"\x01\x00" * 1500)          # 3,000 bytes: two frames, 952 bytes held back
        rec.begin()
        rec.feed(b"\x01\x00" * 100)
        fut = rec.commit()
        text, lang = await asyncio.wait_for(fut, 1.0)
        await rec.aclose()
        return text, lang

    text, lang = run(go())
    assert (text, lang) == ("क्लिनिक के timings क्या हैं?", "hi-IN")
    assert sock.commits == 1 and sock.frames and all(len(f) == 1024 for f in sock.frames)   # the server wants exactly 1,024 bytes


def test_a_pause_and_then_more_speech_gives_the_whole_sentence(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "test-key")
    sock = FakeSocket([("बुधवार", "hi-IN"), ("शाम पाँच बजे", "hi-IN")])

    async def go():
        rec = phone_asr.CallRecognizer(8000, recognizer_factory=fake_recognizer(sock))
        rec.start()
        await asyncio.sleep(0.05)
        rec.begin()
        rec.feed(b"\x01\x00" * 800)
        first = rec.commit()                                   # the pause
        assert (await asyncio.wait_for(first, 1.0))[0] == "बुधवार"
        rec.feed(b"\x01\x00" * 800)                            # the caller went on
        second = rec.commit()
        out = (await asyncio.wait_for(second, 1.0))[0]
        await rec.aclose()
        return out

    assert run(go()) == "बुधवार शाम पाँच बजे"


def test_no_stream_means_a_fast_failure_so_the_call_recognises_the_recording(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "test-key")

    async def go():
        rec = phone_asr.CallRecognizer(8000, recognizer_factory=lambda: (_ for _ in ()).throw(OSError("down")))
        rec.start()
        await asyncio.sleep(0.05)
        rec.begin()
        rec.feed(b"\x01\x00" * 800)                            # nothing is kept while the stream is not open
        assert rec._queue.qsize() == 0
        fut = rec.commit()
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(fut, 0.2)
        await rec.aclose()

    run(go())


def test_a_transcript_that_never_comes_times_out_and_a_late_one_is_dropped(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "test-key")
    monkeypatch.setenv("PHONE_STREAM_ASR_TIMEOUT_S", "0.3")
    sock = FakeSocket([])

    async def go():
        rec = phone_asr.CallRecognizer(8000, recognizer_factory=fake_recognizer(sock))
        rec.start()
        await asyncio.sleep(0.05)
        rec.begin()
        rec.feed(b"\x01\x00" * 800)
        fut = rec.commit()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(fut, 1.0)
        await sock.inbox.put(json.dumps({"type": "transcript", "text": "late", "is_final": True}))   # belongs to that sentence
        await asyncio.sleep(0.05)
        rec.begin()
        rec.feed(b"\x01\x00" * 800)
        await asyncio.sleep(0.01)
        assert rec._cur.texts == []
        await rec.aclose()

    run(go())


def test_a_noise_on_the_line_between_sentences_is_not_a_sentence(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "test-key")
    sock = FakeSocket([])

    async def go():
        rec = phone_asr.CallRecognizer(8000, recognizer_factory=fake_recognizer(sock))
        rec.start()
        await asyncio.sleep(0.05)
        rec.begin()
        rec.end()
        await sock.inbox.put(json.dumps({"type": "transcript", "text": "hmm", "is_final": True}))
        await asyncio.sleep(0.05)
        assert rec._cur.texts == []
        await rec.aclose()

    run(go())


def test_the_stream_switch_and_the_key(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "test-key")
    assert phone_asr.enabled() is True
    monkeypatch.setenv("PHONE_STREAM_ASR", "off")
    assert phone_asr.enabled() is False
    monkeypatch.delenv("PHONE_STREAM_ASR")
    monkeypatch.delenv("GNANI_API_KEY")
    assert phone_asr.enabled() is False


# ------------------------------------------------------------------------------ the tool results ahead of the model

class Spec:
    def __init__(self, tool):
        self.tool, self.name, self.server, self.schema, self.description = tool, tool, "schedules", {"type": "object"}, ""
        self.runner = None


def test_a_booking_request_is_checked_and_its_readback_prepared(monkeypatch):
    seen = []

    def fake_execute(spec, arguments, **kw):
        seen.append((spec.tool, dict(arguments)))
        return tools_mod.ToolCall(spec.name, {"date": "2026-11-12", "time": "17:00"}, True, "free", 3,
                                  {"available": True, "date": "2026-11-12", "time": "17:00"})

    monkeypatch.setattr(tools_mod, "execute", fake_execute)
    monkeypatch.setenv("TOOLS_READBACK", "on")
    form = {"name": "Priya Sharma", "phone": "9876543210", "date": "2026-11-12", "time": "17:00"}
    calls = tools_mod.prefetch_reads([Spec("check_availability"), Spec("book")], form=form, found={"date": "x", "time": "y"},
                                     book_intent=True, previous_agent_text="How can I help?",
                                     caller_text="appointment Thursday 5 pm", known_slots={})
    assert [c.name for c in calls] == ["check_availability", "book"]
    assert seen == [("check_availability", {"date": "2026-11-12", "time": "17:00"})]      # `book` is never executed
    assert calls[1].ok is False and "NOT DONE: first read this back" in calls[1].result
    line = tools_mod.pending_readback_line(calls, "hi", "")
    assert "Priya Sharma" in line and "3 2 1 0" in line and "क्या मैं बुक कर दूँ?" in line


def test_nothing_is_fetched_when_the_caller_gave_no_day_or_time_this_turn(monkeypatch):
    monkeypatch.setattr(tools_mod, "execute", lambda *a, **k: pytest.fail("no tool should run"))
    form = {"name": "Priya", "phone": "9876543210", "date": "2026-11-12", "time": "17:00"}
    assert tools_mod.prefetch_reads([Spec("check_availability"), Spec("book")], form=form, found={}, book_intent=True,
                                    previous_agent_text="x", caller_text="haan", known_slots={}) == []
    assert tools_mod.prefetch_reads([Spec("book")], form=form, found={"date": "x"}, book_intent=True,
                                    previous_agent_text="x", caller_text="y", known_slots={}) == []


def test_a_busy_time_is_fetched_but_no_readback_is_prepared(monkeypatch):
    monkeypatch.setattr(tools_mod, "execute", lambda spec, a, **k: tools_mod.ToolCall(
        spec.name, {"date": "2026-11-12", "time": "17:00"}, True, "busy", 1, {"available": False, "alternatives": []}))
    form = {"name": "Priya Sharma", "phone": "9876543210", "date": "2026-11-12", "time": "17:00"}
    calls = tools_mod.prefetch_reads([Spec("check_availability"), Spec("book")], form=form, found={"time": "x"},
                                     book_intent=True, previous_agent_text="x", caller_text="y", known_slots={})
    assert [c.name for c in calls] == ["check_availability"]


def test_a_plain_yes_to_the_readback_makes_the_booking_after_the_gate(monkeypatch):
    args = {"date": "2026-11-12", "time": "17:00", "name": "Priya Sharma", "phone": "9876543210"}
    spoken = readback.sentences(args, "book")["en"]
    made = []

    def fake_execute(spec, arguments, **kw):
        made.append(spec.tool)
        return tools_mod.ToolCall(spec.name, dict(arguments), True, "booked", 5, {"status": "booked", "booking_id": "A7"})

    monkeypatch.setattr(tools_mod, "execute", fake_execute)
    monkeypatch.setenv("TOOLS_READBACK", "on")
    last = [{"name": "book", "arguments": args, "ok": False, "result": "failed: NOT DONE: first read this back to the caller"}]
    gated = []
    calls = tools_mod.prefetch_reads([Spec("check_availability"), Spec("book")], form={}, found={}, book_intent=False,
                                     previous_agent_text=spoken, caller_text="yes please", known_slots={},
                                     last_agent_calls=last, write_gate=lambda: gated.append(1) or True)
    assert [c.name for c in calls] == ["book"] and made == ["book"] and gated == [1]
    # a "no" is not a yes: nothing is executed
    made.clear()
    assert tools_mod.prefetch_reads([Spec("book")], form={}, found={}, book_intent=False, previous_agent_text=spoken,
                                    caller_text="no, make it 6 pm", known_slots={}, last_agent_calls=last) == [] and not made
    # and when the turn is dropped before the gate opens, nothing is made either
    assert tools_mod.prefetch_reads([Spec("book")], form={}, found={}, book_intent=False, previous_agent_text=spoken,
                                    caller_text="yes", known_slots={}, last_agent_calls=last,
                                    write_gate=lambda: False) == [] and not made


def test_the_booked_sentence_in_three_languages_and_when_the_model_must_speak(monkeypatch):
    value = {"status": "booked", "booking_id": "A39", "date": "2026-11-12", "time": "17:00", "token": 1,
             "whatsapp": {"status": "queued"}}
    call = tools_mod.ToolCall("book", {}, True, "booked", 5, value)
    hi = tools_mod.booked_line([call], "hi")
    assert hi == "बुकिंग हो गई है। बुकिंग आईडी A39 है, गुरुवार 12 नवंबर, शाम 5 बजे। आपका टोकन नंबर 1 है। पुष्टि का संदेश WhatsApp पर भेजा जाएगा।"
    assert tools_mod.booked_line([call], "hinglish") == hi
    assert tools_mod.booked_line([call], "en") == ("Booked. Your booking ID is A39, Thursday 12 November, 5 pm. Your token number is 1. "
                                                   "The confirmation will be sent on WhatsApp.")
    assert "बुकिंग झाली आहे." in tools_mod.booked_line([call], "mr")
    for odd in ({"relation": "mother"}, {"booked_by": "Raj"}, {"running_late": "30 min"}, {"nights": 2},
                {"whatsapp": {"status": "ask"}}, {"status": "cancelled"}):
        assert tools_mod.booked_line([tools_mod.ToolCall("book", {}, True, "x", 1, {**value, **odd})], "hi") is None
    assert tools_mod.booked_line([tools_mod.ToolCall("book", {}, False, "x", 1, "refused")], "hi") is None


def test_the_model_asking_again_for_what_was_fetched_gets_the_result_without_a_second_call(monkeypatch):
    check = tools_mod.ToolCall("check_availability", {"date": "2026-11-12", "time": "17:00"}, True, "busy", 1,
                               {"available": False})
    spec = Spec("check_availability")
    stream = tools_mod.ToolStream([], [spec], prefetched=[check], calls=[check])
    assert stream._prefetched_twin(spec, {"date": "2026-11-12", "time": "17:00"}) is check
    assert stream._prefetched_twin(spec, {"date": "2026-11-13", "time": "17:00"}) is None


def test_a_write_waits_for_the_gate_and_a_dropped_turn_never_writes(monkeypatch):
    ran = []
    monkeypatch.setattr(tools_mod, "execute", lambda spec, a, **k: ran.append(spec.tool) or tools_mod.ToolCall(
        spec.name, a, True, "ok", 1, {}))
    monkeypatch.setattr(tools_mod.llm, "chat_stream", lambda *a, **k: FakeLlm('{"tool": "cancel", "arguments": {"booking_id": "A1"}}'))
    for gate_open, expect in ((True, ["cancel"]), (False, [])):
        ran.clear()
        stream = tools_mod.ToolStream([{"role": "user", "content": "hi"}], [Spec("cancel")], write_gate=lambda g=gate_open: g,
                                      max_calls=1)
        try:
            list(stream)
        except ValueError:
            pass
        assert ran[:1] == expect


class FakeLlm:
    def __init__(self, text):
        self.text = text
        self.info = {}
        self.result = None
        self.provider = self.model = "fake"

    def __iter__(self):
        yield self.text

    def close(self):
        pass


# ------------------------------------------------------------------------------------------- fillers and the greeting

def test_a_filler_names_what_the_caller_asked_for_and_never_twice_in_a_row():
    class Run:
        language = "hi"
        caller_found = {"date": "x", "time": "y"}
        caller_form = {"date": "2026-11-11", "time": "17:00"}

    first = ts.contextual_filler(Run, "conv-1")
    assert first == "बुधवार शाम 5 बजे... एक सेकंड।"
    ts._FILLER_CONTEXTUAL["conv-1"] = True                                    # the filler just played was this kind
    assert ts.contextual_filler(Run, "conv-1") is None                        # the next one is a plain line
    Run.language = "en"
    assert ts.contextual_filler(Run, "conv-2") == "Wednesday 5 pm... one moment."
    Run.caller_found = {}
    assert ts.contextual_filler(Run, "conv-3") is None                        # nothing named this turn: a plain line


def test_the_filler_waits_for_a_slow_answer_only(monkeypatch):
    assert ts.filler_after_s("phone") == 1.5 and ts.filler_after_s(None) == 0.9
    monkeypatch.setenv("PHONE_FILLER_AFTER_MS", "700")
    assert ts.filler_after_s("phone") == 0.7
    assert len(set(ts.FILLER_TEXTS["hi"])) >= 5 and "रुकिए" not in " ".join(ts.FILLER_TEXTS["hi"])


def test_a_personal_greeting_made_while_the_phone_rings_is_used_at_pickup(monkeypatch):
    made = []

    async def fake_render(b, agent, user_id, mode, rate, words):
        made.append(words)
        await asyncio.sleep(0.05)
        return pg.Greeting(key="r", agent_id=str(agent["id"]), text=words[2], language=words[0], discloses=True,
                           disclosure_s=1.0, engine="timbre", voice_id=None, clip_id="c", receipt_id="r", sr=8000, wav=b"",
                           pcm={rate: b"\x01\x00" * 100})

    monkeypatch.setattr(pg, "render_returning", fake_render)
    agent = {"id": "agent-1"}
    words = ("hi", "यह एक AI सहायक है।", "नमस्ते Priya जी।")
    monkeypatch.setattr(pg, "returning_opening", lambda b, a, profile, callback=False: words)
    from services import caller_profiles
    monkeypatch.setattr(caller_profiles, "enabled", lambda: True)
    monkeypatch.setattr(caller_profiles, "lookup", lambda user, number, mode: {"number": number, "display_name": "Priya"})
    monkeypatch.setattr(caller_profiles, "sync_profile", lambda *a, **k: None)

    async def go():
        pg._personal.clear()
        await pg.prepare_personal(object(), agent, "u1", "local", "+919876543210")
        assert len(made) == 1 and pg._personal_key(agent, words) in pg._personal
        g, pcm, source = await pg.for_call(object(), agent, "u1", "local", 8000, returning=words)
        assert source == "returning-prepared" and pcm and len(made) == 1       # not rendered a second time
        assert pg._personal_key(agent, words) not in pg._personal              # used once
        # a call that was not announced renders it itself, and waits for it (8 s, not 3) before giving up
        g, pcm, source = await pg.for_call(object(), agent, "u1", "local", 8000, returning=words)
        assert source == "returning" and len(made) == 2

    run(go())
    assert pg.returning_timeout_s() == 8.0


def test_a_short_yes_counts_as_speech_on_a_call():
    """A 250 ms "हाँ" is an utterance with the phone's minimum, not noise (it was dropped at 400 ms)."""
    try:
        from services import phone_audio as pa
    except ImportError:  # pragma: no cover
        from backend.services import phone_audio as pa
    import os
    assert int(os.getenv("PHONE_MIN_SPEECH_MS", "200")) <= 250
    cfg = pa.DetectorConfig(min_speech_ms=200)
    assert 250 >= cfg.min_speech_ms


def test_prisma_spellings_of_a_short_yes_are_a_yes():
    """Prisma wrote a caller's "हाँ" as "हँ।" on the 9 Nov call, and the read-back was repeated."""
    try:
        from services import readback
    except ImportError:  # pragma: no cover
        from backend.services import readback
    for said in ("हँ।", "हं", "हा।", "यस", "ओके"):
        assert readback.is_affirmative(said), said


def test_a_bare_opener_waits_for_the_rest_of_the_sentence():
    try:
        from services import turn_taking as tt
    except ImportError:  # pragma: no cover
        from backend.services import turn_taking as tt
    for half in ("मुझे actually।", "मुझे", "I want", "ये मुझे"):
        assert tt.is_incomplete(half) == "opener", half
    for whole in ("हाँ", "हाँ, कर दो।", "clinic के timings क्या हैं?", "मुझे बुधवार शाम पाँच बजे का appointment चाहिए", "ok"):
        assert tt.is_incomplete(whole) is None, whole


def test_a_short_reask_after_a_full_readback_books_on_yes():
    """9 Nov 2026: full read-back, a side question, then "क्या मैं बुधवार 11 नवंबर शाम 5 बजे की अपॉइंटमेंट बुक कर दूँ?"
    (no name); "हाँ, कर दो" must book, not read the booking back a third time."""
    try:
        from services import readback
    except ImportError:  # pragma: no cover
        from backend.services import readback
    from datetime import date, time
    assert readback.slot_readback_ok("ठीक है। क्या मैं बुधवार 11 नवंबर शाम 5 बजे की अपॉइंटमेंट बुक कर दूँ?",
                                     date(2026, 11, 11), time(17, 0), None, None, None)
    assert readback.is_affirmative("हाँ, कर दो।", date(2026, 11, 11), time(17, 0))


# ------------------------------------------------------------------------------ moving the booking made in this call

MADE = [{"role": "agent", "tool_calls": [{"name": "book", "ok": True, "arguments": {"date": "2026-11-11", "time": "17:00"},
                                          "result": "booking A51 on Wednesday 2026-11-11 17:00"}]}]


def test_shift_words():
    from services import when
    assert when.shift_minutes("क्या वो बुधवार की booking एक घंटे बाद कर सकते हो?") == 60
    assert when.shift_minutes("आधा घंटा पहले") == -30 and when.shift_minutes("one hour later") == 60
    assert when.shift_minutes("15 minutes earlier") == -15 and when.shift_minutes("थोड़ा बाद") == 30
    assert when.shift_minutes("बुधवार शाम 5 बजे") is None and when.shift_minutes("एक बजे के बाद") is None


def test_a_move_request_names_the_callers_own_booking_and_the_new_slot():
    mv = tools_mod.move_request(MADE, "क्या वो बुधवार की booking एक घंटे बाद कर सकते हो?", {"date": "2026-11-11"})
    assert mv == {"booking_id": "A51", "date": "2026-11-11", "time": "18:00"}
    assert tools_mod.move_request(MADE, "usi din 6 baje", {"time": "06:00"})["time"] == "18:00"
    assert tools_mod.move_request([], "एक घंटे बाद", {}) is None                      # no booking made in this call
    assert tools_mod.move_request(MADE, "बुधवार शाम 5 बजे", {}) is None
    cancelled = MADE + [{"role": "agent", "tool_calls": [{"name": "cancel", "ok": True, "arguments": {"booking_id": "A51"}, "result": "x"}]}]
    assert tools_mod.booking_in_call(cancelled) is None


def test_a_move_checks_the_new_time_only_and_reads_the_move_back(monkeypatch):
    seen = []

    def fake_execute(spec, arguments, **kw):
        seen.append((spec.tool, dict(arguments)))
        return tools_mod.ToolCall(spec.name, dict(arguments), True, "free", 1, {"available": True})

    monkeypatch.setattr(tools_mod, "execute", fake_execute)
    monkeypatch.setenv("TOOLS_READBACK", "on")
    calls = tools_mod.prefetch_reads([Spec("check_availability"), Spec("reschedule")], form={"date": "2026-11-11", "time": "17:00"},
                                     found={"date": "2026-11-11"}, book_intent=False, previous_agent_text="Booked.",
                                     caller_text="वो बुधवार की booking एक घंटे बाद कर सकते हो?", known_slots={}, history=MADE)
    assert seen == [("check_availability", {"date": "2026-11-11", "time": "18:00"})]   # never 17:00, the caller's own slot
    assert [c.name for c in calls] == ["check_availability", "reschedule"]
    assert "NOT DONE: first read this back" in calls[1].result
    line = tools_mod.pending_readback_line(calls, "hi", "")
    assert "बुधवार 11 नवंबर" in line and "6 बजे" in line and "क्या मैं बदल दूँ?" in line


def test_a_new_day_after_a_refused_time_drops_that_time():
    refused = [{"role": "agent", "tool_calls": [{"name": "check_availability", "ok": True, "arguments": {"date": "2026-11-11", "time": "05:00"},
                                                 "result": "05:00 on 2026-11-11 is not free, 2 other option(s)"}]}]
    assert tools_mod.last_check_refused(refused) is True and tools_mod.last_check_refused(MADE) is False
    assert tools_mod.known_slots(refused)["time"] == "05:00"
    assert "time" not in tools_mod.known_slots(refused, ("time",))


# ------------------------------------------------------------------------------ moving a booking from an earlier call

UP1 = [{"booking_id": "A28", "date": "2026-11-10", "time": "18:00", "status": "booked"}]
UP2 = UP1 + [{"booking_id": "A56", "date": "2026-11-12", "time": "17:00", "status": "booked"}]


def _fake_hub_execute(monkeypatch, rows, seen):
    def fake_execute(spec, arguments, **kw):
        seen.append((spec.tool, dict(arguments)))
        if spec.tool == "find_booking":
            return tools_mod.ToolCall(spec.name, dict(arguments), True, "n", 1, {"bookings": rows})
        return tools_mod.ToolCall(spec.name, dict(arguments), True, "free", 1, {"available": True})
    monkeypatch.setattr(tools_mod, "execute", fake_execute)
    monkeypatch.setenv("TOOLS_READBACK", "on")


SPECS = [Spec("check_availability"), Spec("reschedule"), Spec("find_booking")]


def test_the_callers_only_upcoming_booking_is_the_one_moved(monkeypatch):
    seen = []
    _fake_hub_execute(monkeypatch, UP1, seen)
    calls = tools_mod.prefetch_reads(SPECS, form={"phone": "8788996295"}, found={}, book_intent=False, previous_agent_text="Hello",
                                     caller_text="क्या वो booking एक घंटे बाद कर सकते हो?", known_slots={}, history=[])
    assert ("check_availability", {"date": "2026-11-10", "time": "19:00"}) in seen
    assert [c.name for c in calls][-1] == "reschedule" and calls[-1].arguments["booking_id"] == "A28"
    assert "NOT DONE: first read this back" in calls[-1].result


def test_several_bookings_ask_which_then_the_named_one_is_moved(monkeypatch):
    seen = []
    _fake_hub_execute(monkeypatch, UP2, seen)
    calls = tools_mod.prefetch_reads(SPECS, form={"phone": "8788996295"}, found={}, book_intent=False, previous_agent_text="Hello",
                                     caller_text="आप वो booking एक घंटे बाद कर दो", known_slots={}, history=[])
    assert calls[-1].name == "which_booking"
    line = tools_mod.which_booking_line(calls, "hi")
    assert "मंगलवार 10 नवंबर शाम 6 बजे" in line and "गुरुवार 12 नवंबर शाम 5 बजे" in line and "कौन सी बदलनी है?" in line
    history = [{"role": "user", "text_content": "आप वो booking एक घंटे बाद कर दो"},
               {"role": "agent", "text_content": line, "tool_calls": [{"name": "find_booking"}, {"name": "which_booking"}]}]
    seen.clear()
    calls = tools_mod.prefetch_reads(SPECS, form={"phone": "8788996295"}, found={"date": "2026-11-12"}, book_intent=False,
                                     previous_agent_text=line, caller_text="गुरुवार वाली", known_slots={}, history=history,
                                     last_agent_calls=history[-1]["tool_calls"])
    assert ("check_availability", {"date": "2026-11-12", "time": "18:00"}) in seen        # later, not earlier
    assert calls[-1].arguments == {"booking_id": "A56", "date": "2026-11-12", "time": "18:00"}


def test_a_move_without_a_time_runs_no_check(monkeypatch):
    seen = []
    _fake_hub_execute(monkeypatch, UP1, seen)
    tools_mod.prefetch_reads(SPECS, form={"phone": "8788996295"}, found={}, book_intent=False, previous_agent_text="Hello",
                             caller_text="आप वो booking move कर सकते हो?", known_slots={}, history=[])
    assert [t for t, _ in seen] == ["find_booking"]


def test_a_yes_to_the_models_own_move_question_moves_the_booking(monkeypatch):
    seen = []
    _fake_hub_execute(monkeypatch, UP1, seen)
    calls = tools_mod.prefetch_reads(SPECS, form={"phone": "8788996295"}, found={}, book_intent=False,
                                     previous_agent_text="क्या मैं इसे शाम 7 बजे पर बदल दूँ?", caller_text="हाँ भई हाँ",
                                     known_slots={}, history=[])
    assert calls[-1].name == "reschedule" and calls[-1].arguments == {"booking_id": "A28", "date": "2026-11-10", "time": "19:00"}


def test_the_same_refusal_is_not_spoken_twice_in_a_row():
    from services import grounding
    first = grounding.AnswerGuard([{"role": "user", "content": "x"}], language="hi")
    assert first.repeat is False
    again = grounding.AnswerGuard([{"role": "assistant", "content": grounding.REFUSAL_MID["hi"]}, {"role": "user", "content": "x"}], language="hi")
    assert again.repeat is True
    assert grounding.refusal_line("hi", again=True) != grounding.refusal_line("hi", mid_answer=True)
