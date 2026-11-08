"""The problems found on the live phone call of 8 Nov 2026 (hosted language model, Gnani Prisma and Timbre, a clinic desk, a known
caller): the number asked about more than once, the booking read back twice (the model checked the schedule again after the
caller's yes, and Prisma's "yeah" heard as "या" was not a yes), the reply language switching on one short token, ISO dates
spoken aloud, and a first answer that took 5 to 7 s. Nothing here touches the network."""
import asyncio
import json
from datetime import date, time
from types import SimpleNamespace

import pytest

from services import (
    booking_notify, caller_memory as cm, language_policy as lp, readback as rb, speech_numbers, tools,
    turn_pipeline,
)
from services import llm  # noqa: E402
from tests.test_tools_client import BOOKED, FakeHub

FULL = ("Tuesday 10 November at 5 pm, for Ayush, phone number ending 6 2 9 5, and I'll send the confirmation on WhatsApp. "
        "Shall I book it?")
SHORT = "Not yet. I still need your confirmation. Shall I book Tuesday 10 November at 5 PM for Ayush?"
ARGS = {"name": "Ayush", "phone": "8788996295", "date": "2026-11-09", "time": "17:00"}
BOOK = tools.ToolSpec("appointments", "book", "Book.", {"type": "object", "properties": {
    "name": {"type": "string"}, "phone": {"type": "string"}, "date": {"type": "string"}, "time": {"type": "string"}}})
CHECK = tools.ToolSpec("appointments", "check_availability", "Check.", {"type": "object", "properties": {
    "date": {"type": "string"}, "time": {"type": "string"}}})
YES_WORDS = ["yes", "yeah", "haan", "हाँ", "please do", "sure", "ok", "या", "Yeah, please.", "या, please।", "हाँ, yes।"]


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("TOOLS_READBACK", "on")


@pytest.fixture()
def hub(monkeypatch):
    fake = FakeHub({"book": BOOKED, "check_availability": (True, {"available": True})})
    monkeypatch.setattr(tools, "hub", fake)
    return fake


# ---------------------------------------------------------------------------------------------- 1. the number, once

ASK = "Sure, Ayush! Which day and time? Same number as last time, ending 6295?"


def test_a_yes_to_same_number_settles_the_number_for_the_call_even_with_more_in_the_turn():
    found = cm.extract("Yeah, sure. Monday, it's three pm, is it?", ASK)
    assert found["phone_confirmed"] == "yes"
    form = cm.merge({"name": "Ayush", "phone": "8788996295"}, found)
    slots = cm.prompt_slots(form, {})
    assert slots["phone confirmed"] == "yes" and slots["name"] == "Ayush"
    line = tools.slots_line(slots)
    assert "phone confirmed: yes" in line and "never ask about it again" in line
    assert "ask once, early" not in line                       # no second question about the number
    assert "The name is settled" in line


def test_a_number_from_an_earlier_call_is_asked_once_and_a_number_the_caller_gives_is_settled():
    unconfirmed = tools.slots_line(cm.prompt_slots({"name": "Ayush", "phone": "8788996295"}, {}))
    assert "not yet confirmed: ask once, early" in unconfirmed
    given = cm.extract("my number is 98765 43210", "What is your number?")
    assert given["phone"] == "9876543210" and given["phone_confirmed"] == "yes"
    assert "phone confirmed" not in cm.prompt_slots({"name": "Ayush"}, {})        # nothing to confirm without a number


def test_only_a_yes_to_a_question_about_the_number_confirms_it():
    assert "phone_confirmed" not in cm.extract("yes", "Which day would you like?")
    assert "phone_confirmed" not in cm.extract("no, use 98 something else", ASK)


def test_the_prompt_says_to_ask_once_and_never_repeat_the_number_or_the_name():
    text = tools.tools_prompt([CHECK, BOOK], json_protocol=True)
    assert "confirmed once, early" in text and "phone confirmed" in text and "Never ask for a name the call details already have" in text


# ---------------------------------------------------------------------------------------------- 2. one read-back, then book

def test_the_read_back_is_one_sentence_with_day_date_time_name_number_and_whatsapp(hub):
    c = tools.execute(BOOK, ARGS, previous_agent_text="Would 5 pm on Monday suit you?", caller_text="Yeah, sure.")
    assert not c.ok and c.value.startswith("NOT DONE") and hub.calls == []
    assert ("Monday 9 November at 5 pm, for Ayush, phone number ending 6 2 9 5, and I'll send the confirmation on WhatsApp. "
            "Shall I book it?") in c.value
    spoken = rb.sentences(ARGS, whatsapp=True)["en"]
    assert rb.slot_readback_ok(spoken, date(2026, 11, 9), time(17, 0), "Ayush", "8788996295") and rb.mentions_whatsapp(spoken)


@pytest.mark.parametrize("said", YES_WORDS)
def test_a_yes_after_the_read_back_goes_straight_to_the_booking_tool(hub, said):
    spoken = rb.sentences(ARGS, whatsapp=True)["en"]
    c = tools.execute(BOOK, ARGS, previous_agent_text=spoken, caller_text=said)
    assert c.ok and hub.calls == [("book", {**ARGS})], said


def test_checking_the_schedule_again_after_the_yes_is_refused_and_the_model_is_told_to_book(hub):
    """The cause of the second read-back on the call: the model called check_availability after "Yeah, please."."""
    spoken = rb.sentences(ARGS, whatsapp=True)["en"]
    c = tools.execute(CHECK, {"date": "2026-11-09", "time": "17:00"}, previous_agent_text=spoken, caller_text="Yeah, please.",
                      known_slots={"name": "Ayush", "phone": "8788996295"})
    assert not c.ok and "Call book now" in c.value and "8788996295" in c.value and hub.calls == []
    # a check that is not after a yes still runs (another time, or a question)
    c = tools.execute(CHECK, {"date": "2026-11-09", "time": "18:00"}, previous_agent_text=spoken, caller_text="Yeah, please.")
    assert c.ok
    c = tools.execute(CHECK, {"date": "2026-11-09", "time": "17:00"}, previous_agent_text=spoken, caller_text="is 6 pm free?")
    assert c.ok


def test_a_misheard_yeah_is_a_yes_only_straight_after_a_read_back():
    for said in ("या", "या, please।", "या please", "हाँ, yes।"):
        assert rb.is_affirmative(said, date(2026, 11, 9), time(17, 0)), said
    for said in ("या नहीं", "या कल", "या 6 बजे"):
        assert not rb.is_affirmative(said, date(2026, 11, 9), time(17, 0)), said


# ---------------------------------------------------------------------------------------------- WhatsApp consent from the yes

def test_the_yes_to_a_read_back_that_names_whatsapp_is_the_consent(hub):
    assert booking_notify.consent_state("8788996295") is None and booking_notify.asks_consent("8788996295")
    spoken = rb.sentences(ARGS, whatsapp=True)["en"]
    assert tools.execute(BOOK, ARGS, previous_agent_text=spoken, caller_text="Yeah, please.").ok
    assert booking_notify.consent_state("8788996295") is True
    assert not booking_notify.asks_consent("8788996295")                  # never asked again, and no WhatsApp clause next time
    # the booking now queues its message instead of answering "ask"
    out = booking_notify.booking_changed("booked", {"booking_id": "A27", "phone": "8788996295", "date": "2026-11-09",
                                                    "time": "17:00", "name": "Ayush"})
    assert out["status"] == "queued"


def test_a_read_back_without_whatsapp_does_not_record_consent_and_a_no_never_does(hub):
    plain = rb.sentences(ARGS)["en"]
    assert tools.execute(BOOK, ARGS, previous_agent_text=plain, caller_text="yes").ok
    assert booking_notify.consent_state("8788996295") is None
    spoken = rb.sentences(ARGS, whatsapp=True)["en"]
    assert not tools.execute(BOOK, ARGS, previous_agent_text=spoken, caller_text="no, make it 6 pm").ok
    assert booking_notify.consent_state("8788996295") is None


def test_a_number_that_declined_whatsapp_is_not_asked_again_and_not_overwritten(hub):
    booking_notify.booking_changed("booked", {"booking_id": "A1", "phone": "8788996295"})
    booking_notify.record_consent(False)
    assert booking_notify.consent_state("8788996295") is False and not booking_notify.asks_consent("8788996295")
    assert booking_notify.consent_from_readback("8788996295")["status"] == "already_answered"
    assert booking_notify.consent_state("8788996295") is False


# ---------------------------------------------------------------------------------------------- 3. no language switch on one token

SUPPORTED = ["en", "hi"]


@pytest.mark.parametrize("said", ["या", "या, please।", "हाँ", "हाँ, yes।", "ठीक है", "जी"])
def test_a_short_or_ambiguous_devanagari_token_keeps_an_english_call_english(said):
    d = lp.decide(said, SUPPORTED, None, "Monday 9 November at 5 pm. Shall I book it?")
    assert d.target == "en" and d.reason == "inherit", said


@pytest.mark.parametrize("said", ["yeah", "ok", "yes please", "Monday"])
def test_a_short_english_token_keeps_a_hindi_call_hindi(said):
    d = lp.decide(said, SUPPORTED, None, "सोमवार 9 नवंबर शाम 5 बजे। क्या मैं बुक कर दूँ?")
    assert d.target == "hi", said


def test_a_clear_sentence_in_the_other_language_still_switches():
    d = lp.decide("मुझे कल सुबह का समय चाहिए", SUPPORTED, None, "Which day would you like?")
    assert d.target == "hi" and d.reason == "script"
    d = lp.decide("I would like to book for tomorrow morning", SUPPORTED, None, "आप कौन सा दिन चाहेंगे?")
    assert d.target == "en"


def test_the_first_turn_has_no_call_language_to_keep():
    assert lp.decide("हाँ", SUPPORTED, None, None).target == "hi"


# ---------------------------------------------------------------------------------------------- 4. dates, retrieval, cache

def test_no_iso_date_or_year_is_spoken():
    assert speech_numbers.spoken_dates("Would 5:00 PM on Monday, 2026-11-09, suit you?") == "Would 5:00 PM on Monday 9 November, suit you?"
    assert speech_numbers.spoken_dates("2026-11-09 is free") == "Monday 9 November is free"
    assert "2026" not in speech_numbers.spoken_dates("Monday, 9 November 2026, at 5:00 PM is free.")
    assert "2026" not in speech_numbers.spoken_dates("सोमवार, 2026-11-09 शाम 5 बजे")
    text, _lang = asyncio.run(turn_pipeline.turn_spoken("en", "Booked for 2026-11-09 at 5 pm.", False, use_llm=False))
    assert "2026" not in text and "Monday 9 November" in text


def test_the_availability_result_carries_a_sayable_date():
    from services import schedule_tools
    assert schedule_tools._say_date(date(2026, 11, 9)) == "Monday 9 November"


@pytest.mark.parametrize("said,previous", [
    ("Yeah, sure.", "Shall I book it?"), ("Monday, it's three pm, is it?", ""), ("9876543210", ""), ("या, please।", "Shall I book it?"),
    ("I want to book an appointment", ""), ("Ayush Chaugule", "What is your name?"), ("no", "Shall I book it?"),
    ("Yes, I already said yes.", SHORT), ("Did you do it?", FULL)])
def test_booking_steps_skip_retrieval(said, previous):
    assert turn_pipeline.booking_step(said, previous), said


@pytest.mark.parametrize("said", ["What are your fees?", "Do you take insurance", "where is the clinic", "kya aap Sunday ko khule ho",
                                  "tell me about the doctors and the timings and the costs of everything you offer"])
def test_questions_keep_retrieval(said):
    assert not turn_pipeline.booking_step(said, "How can I help?"), said


def _run(**kw):
    base = dict(local=True, agent_id="a1", agent={"tools": ["schedules"]}, user_text="Yeah, sure.")
    base.update(kw)
    return SimpleNamespace(**base)


def test_retrieval_is_skipped_for_a_booking_step_and_for_an_agent_without_documents(monkeypatch):
    from core import state
    rows = [{"agent_id": "a1"}]
    monkeypatch.setattr(state, "local_db", SimpleNamespace(select=lambda t, where=None, limit=None: [r for r in rows if where(r)]))
    assert asyncio.run(turn_pipeline.retrieval_skip_reason(_run(), "Shall I book it?")) == "booking_step"
    assert asyncio.run(turn_pipeline.retrieval_skip_reason(_run(user_text="What are your fees?"), "")) is None
    rows.clear()
    assert asyncio.run(turn_pipeline.retrieval_skip_reason(_run(user_text="What are your fees?"), "")) == "no_documents"
    monkeypatch.setenv("RETRIEVAL_SKIP", "off")
    assert asyncio.run(turn_pipeline.retrieval_skip_reason(_run(), "Shall I book it?")) is None


MSGS = [{"role": "system", "content": "You are Ria. " * 50}, {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hello."}, {"role": "user", "content": "Context...\nCaller says: book me in"}]


def test_the_request_carries_the_stable_prompt_first_and_no_cache_markers():
    body = llm._request("m", MSGS, temperature=0.2, max_tokens=200, stream=True)
    assert body["messages"][0]["role"] == "system" and body["messages"][0]["content"].startswith("You are Ria.")
    assert body["messages"][-1]["content"].startswith("Context...")      # the volatile part is last
    assert "cache_control" not in json.dumps(body)


# ---------------------------------------------------------------------------------------------- 2b. a yes to the short re-ask

ARGS10 = {"name": "Ayush", "phone": "8788996295", "date": "2026-11-10", "time": "17:00"}
HISTORY = [{"role": "agent", "text_content": FULL}, {"role": "user", "text_content": "Did you do it?"},
           {"role": "agent", "text_content": SHORT}]


def test_a_yes_to_the_short_re_ask_books_at_once_when_the_full_read_back_was_given(hub):
    slots = tools.known_slots(HISTORY)
    c = tools.execute(BOOK, ARGS10, previous_agent_text=SHORT, caller_text="Yes.", known_slots=slots)
    assert c.ok and len(hub.calls) == 1


def test_the_short_re_ask_alone_never_books_without_an_earlier_full_read_back(hub):
    c = tools.execute(BOOK, ARGS10, previous_agent_text=SHORT, caller_text="Yes.",
                      known_slots=tools.known_slots([{"role": "agent", "text_content": "Which day suits you?"}]))
    assert not c.ok and c.value.startswith("NOT DONE") and hub.calls == []


def test_a_changed_detail_after_the_short_re_ask_is_read_back_again(hub):
    slots = tools.known_slots(HISTORY)
    c = tools.execute(BOOK, {**ARGS10, "time": "18:00"}, previous_agent_text=SHORT, caller_text="Yes.", known_slots=slots)
    assert not c.ok and hub.calls == []
    c = tools.execute(BOOK, ARGS10, previous_agent_text=SHORT, caller_text="No, make it 6.", known_slots=slots)
    assert not c.ok and hub.calls == []


def test_the_agent_turn_history_is_never_shown_to_the_model_as_a_slot():
    slots = tools.known_slots(HISTORY)
    assert "Shall I book" not in tools.slots_line(slots)
    assert tools.slots_line(slots) == ""
    assert tools.READBACKS_KEY not in cm.prompt_slots({"name": "Ayush"}, slots)


def test_every_llm_request_logs_its_cached_tokens(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="vakya.llm"):
        llm.log_usage({"prompt_tokens": 40, "completion_tokens": 9, "prompt_tokens_details": {"cached_tokens": 12}}, "stream", "stop")
        llm.log_usage({"prompt_tokens": 7}, "complete")
    assert "input=40 cached=12 output=9 finish=stop" in caplog.text and "input=7 cached=0 output=0 finish=-" in caplog.text


# ------------------------------------------------- 9. moving or cancelling: one read-back, the first yes executes

from services import schedule_bookings as _sb  # noqa: E402
from tests import test_reschedule as _tr  # noqa: E402
from tests.test_reschedule import clinic  # noqa: E402,F401  (the fixture: a clinic desk with a week of hours)

PHONE_ASK = " Also, is the number ending 6295 correct?"


def _move(c, prior, said, time="18:00"):
    return tools.execute(_tr.spec("reschedule"), {"booking_id": "A10", "date": _tr.THURSDAY, "time": time},
                         previous_agent_text=prior, caller_text=said)


def test_the_phone_question_never_blocks_a_move_and_the_first_yes_runs_it(clinic):
    turns = [
        "Got it, the Thursday 5 November booking at 5 PM. Shall I move it to 6 PM on Thursday 5 November?" + PHONE_ASK,
        "The Thursday booking is A10, Thursday 5 November at 5 PM. Shall I move it to 6 PM on Thursday 5 November?",
        "Moving A10 from Thursday 5 November 5 PM to 6 PM. Shall I go ahead?",
        "Moving your booking to Thursday 5 November at 6 pm. Shall I go ahead?",
    ]
    for prior in turns:
        _tr.make(clinic, at="17:00", name="Ayush") if not any(
            b["booking_id"] == "A10" for b in _tr.ap.all_bookings()) else None
        c = _move(clinic, prior, "Yes, sir.")
        assert c.ok, prior
        assert _tr.stored("A10")["time"] == "18:00"
        _sb.reschedule("A10", _tr.AGENT, day=_tr.date.fromisoformat(_tr.THURSDAY), at="17:00", caller=True, now=_tr.NOW)


@pytest.mark.parametrize("said", ["Yes, sir.", "Yes, I said.", "Yes ma'am, yes please."])
def test_every_yes_wording_executes_a_move(clinic, said):
    _tr.make(clinic, at="17:00", name="Ayush")
    c = _move(clinic, "Shall I move it to 6 PM on Thursday 5 November?", said)
    assert c.ok and _tr.stored("A10")["time"] == "18:00"


def test_a_changed_target_time_is_read_back_again(clinic):
    _tr.make(clinic, at="17:00", name="Ayush")
    prior = "Got it, the booking at 5 PM. Shall I move it to 6 PM on Thursday 5 November?" + PHONE_ASK
    c = _move(clinic, prior, "Yes, sir.", time="19:00")
    assert not c.ok and c.value.startswith("NOT DONE")
    assert _tr.stored("A10")["time"] == "17:00"
    c = _move(clinic, prior, "No, make it 7.")
    assert not c.ok and _tr.stored("A10")["time"] == "17:00"


def test_a_cancel_with_a_phone_question_in_the_ask_runs_on_the_first_yes(clinic):
    _tr.make(clinic, at="17:00", name="Ayush")
    ask = "Shall I cancel booking A10, Thursday 5 November at 5 pm?" + PHONE_ASK
    c = tools.execute(_tr.spec("cancel"), {"booking_id": "A10"}, previous_agent_text=ask, caller_text="Yes, sir.")
    assert c.ok and _tr.stored("A10")["status"] == "cancelled"


def test_the_prompt_never_asks_to_confirm_the_number_for_a_move_or_a_cancel():
    text = tools.tools_prompt([_tr.spec("reschedule"), _tr.spec("cancel")], json_protocol=False)
    assert "Never ask about the phone number for a move" in text and "first yes" in text


def test_the_memory_model_skips_bare_yes_turns_but_reads_real_ones():
    for said in ("Yes, sir.", "Yes, I said.", "Yes ma'am, yes please."):
        assert cm.llm_worth_reading(said) is False
    assert cm.llm_worth_reading("I want to move my Tuesday appointment to six PM.") is True
    assert cm.llm_worth_reading("my number is 98765 43210") is True
    assert cm.llm_worth_reading("Tuesday", {"date": "2026-11-10"}) is False


# ------------------------------------------------------------------ 4. the greeting reads the live bookings, not the profile
from datetime import datetime  # noqa: E402

from services import caller_profiles as cp  # noqa: E402

NUM = "+918788996295"
NOW = datetime(2026, 11, 8, 12, 0)
STALE = {"owner": "u", "number": NUM, "display_name": "Ayush", "preferred_language": "en",
         "last_booking": {"date": "2026-11-10", "time": "17:00"}}


def _row(bid, date_, time_, status="booked", phone="8788996295"):
    return {"booking_id": bid, "phone": phone, "status": status, "date": date_, "time": time_, "agent_id": "a1",
            "start": f"{date_}T{time_}", "end": f"{date_}T{time_[:2]}:30".replace(f"{time_[:2]}:30", f"{int(time_[:2]):02d}:30")}


def _greet(rows):
    prof = cp.with_live_booking(dict(STALE), "a1", NOW, rows)
    return cp.returning_greeting(prof, ["en"], now=NOW)[1]


def test_the_greeting_says_the_new_time_after_a_reschedule():
    rows = [_row("A28", "2026-11-10", "18:00"), _row("A27", "2026-11-09", "17:00", status="cancelled")]
    words = _greet(rows)
    assert "6 PM" in words and "5 PM" not in words


def test_the_greeting_takes_the_nearest_upcoming_booking_and_skips_cancelled_ones():
    rows = [_row("A28", "2026-11-10", "18:00"), _row("A27", "2026-11-09", "17:00"), _row("A20", "2026-11-08", "09:00"),
            _row("A19", "2026-11-08", "15:00", status="cancelled"), _row("A99", "2026-11-09", "10:00", phone="9999999999")]
    assert "tomorrow at 5 PM" in _greet(rows)


def test_the_greeting_does_not_mention_a_cancelled_booking():
    words = _greet([_row("A28", "2026-11-10", "17:00", status="cancelled")])
    assert "Tuesday" not in words and "appointment" not in words and words.startswith("Hello Ayush")


def test_the_prompt_block_lists_live_bookings_and_forbids_the_phone_question_and_a_new_booking():
    rows = [_row("A28", "2026-11-10", "18:00"), _row("A27", "2026-11-09", "17:00")]
    up = cp.live_bookings(NUM, "a1", NOW, rows)
    assert [b["booking_id"] for b in up] == ["A27", "A28"]
    block = cp.block_text(dict(STALE), up)
    assert "A27 Monday 2026-11-09 at 17:00; A28 Tuesday 2026-11-10 at 18:00" in block
    assert "never ask about the phone number" in block and "not a new one" in block
    assert "what they want to do with it" in block
    assert "last booking" not in block


def test_a_caller_identified_by_caller_id_with_bookings_has_the_number_settled():
    form = cp.prefill_form(dict(STALE), confirmed=True)
    assert form["phone_confirmed"] == "yes"
    slots = cm.prompt_slots(cm.merge(form, {}), {})
    line = tools.slots_line(slots)
    assert "never ask about it again" in line and "ask once, early" not in line
    assert "phone_confirmed" not in cp.prefill_form(dict(STALE))


# ------------------------------------------------------------------ 5. the profile carries an explicit status per booking
def _moved(bid, date_, time_, prev_time):
    r = _row(bid, date_, time_)
    r["moved_from"] = {"date": date_, "time": prev_time}
    r["moved_at"] = "2026-11-08T10:00:00"
    return r


def test_sync_marks_a_moved_booking_rescheduled_and_a_cancelled_one_cancelled_and_follows_them():
    rows = [_moved("A28", "2026-11-10", "18:00", "17:00"), _row("A27", "2026-11-09", "17:00", status="cancelled")]
    prof = dict(STALE, people=[{"name": "Ayush", "last_booking": {"date": "2026-11-10", "time": "17:00"}}],
                last_booking={"date": "2026-11-10", "time": "17:00"})
    out = cp.sync_bookings(prof, "a1", NOW, rows)
    by = {b["booking_id"]: b for b in out["bookings"]}
    assert by["A28"]["status"] == "rescheduled" and by["A28"]["time"] == "18:00" and by["A28"]["previous"]["time"] == "17:00"
    assert by["A27"]["status"] == "cancelled"
    assert out["last_booking"]["time"] == "18:00" and out["people"][0]["last_booking"]["time"] == "18:00"
    block = cp.block_text(cp.clean_profile(out))
    assert "Bookings: A28 Tue 10 Nov 6 PM (rescheduled from 5 PM); A27 Mon 9 Nov 5 PM (cancelled)" in block
    assert "Monday" not in cp.returning_greeting(cp.clean_profile(out), ["en"], now=NOW)[1]


def test_sync_profile_repairs_the_stored_profile_and_a_cancel_clears_the_last_booking(tmp_path, monkeypatch):
    monkeypatch.setenv("VAKYA_CALLER_PROFILES_FILE", str(tmp_path / "p.json"))
    store = cp.get_store("local")
    store.put(cp.clean_profile(dict(STALE, last_call_at=cp._now_iso())))
    rows = [_moved("A28", "2026-11-10", "18:00", "17:00"), _row("A27", "2026-11-09", "17:00", status="cancelled")]
    fixed = cp.sync_profile("u", NUM, "a1", rows=rows, now=NOW)
    saved = store.get("u", cp.number_of(NUM))
    assert saved["last_booking"]["time"] == "18:00" and [b["status"] for b in saved["bookings"]] == ["cancelled", "rescheduled"]
    assert fixed["bookings"] == saved["bookings"]
    gone = cp.sync_profile("u", NUM, "a1", rows=[_row("A28", "2026-11-10", "18:00", status="cancelled")], now=NOW)
    assert gone["last_booking"] is None and gone["bookings"][0]["status"] == "cancelled"
