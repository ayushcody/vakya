"""The problems found on the live phone call of 8 Nov 2026 (hosted language model, Gnani Prisma and Timbre, a clinic desk, a known
caller): the number asked about more than once, the booking read back twice (the model checked the schedule again after the
caller's yes, and Prisma's "yeah" heard as "या" was not a yes), the reply language switching on one short token, ISO dates
spoken aloud, and a first answer that took 5 to 7 s. Nothing here touches the network."""
import asyncio
from datetime import date, time
from types import SimpleNamespace

import pytest

from services import (
    booking_notify, caller_memory as cm, primary_llm, language_policy as lp, readback as rb, speech_numbers, tools,
    turn_pipeline,
)
from tests.test_tools_client import BOOKED, FakeHub

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
    ("I want to book an appointment", ""), ("Ayush Chaugule", "What is your name?"), ("no", "Shall I book it?")])
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


def test_the_stable_prompt_has_a_cache_breakpoint_and_the_volatile_part_is_after_it(monkeypatch):
    params = primary_llm.build_params(MSGS, max_tokens=200, tools=None)
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"} and params["system"][0]["text"].startswith("You are Ria.")
    # the call so far (the message before the caller's newest one) is the second breakpoint; the newest message is not cached
    assert params["messages"][-2]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in params["messages"][-1]["content"][-1]
    monkeypatch.setenv("LLM_PROMPT_CACHE", "off")
    off = primary_llm.build_params(MSGS, max_tokens=200)
    assert isinstance(off["system"], list) and "cache_control" not in off["system"][0]
    assert all("cache_control" not in b for m in off["messages"] for b in m["content"])


def test_the_cache_usage_is_reported():
    usage = SimpleNamespace(input_tokens=30, output_tokens=5, cache_read_input_tokens=2400, cache_creation_input_tokens=0)
    assert primary_llm.usage_dict(usage)["cache_read_input_tokens"] == 2400
