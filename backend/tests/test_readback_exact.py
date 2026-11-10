"""The exact read-back rule (services/readback.py, used by services/tools.py): before `book` runs, the agent's immediately
preceding turn names THIS booking (exact date, exact time and no other, the name, the last digits of the phone) and asks,
and the caller's latest turn is a yes. An offer of two times is never a read-back."""
from datetime import date, time

import pytest

from services import readback as rb, tools
from tests.test_tools_client import BOOKED, FakeHub

D, T = date(2026, 11, 12), time(19, 0)           # Thursday 12 November, 7 pm
NAME, PHONE = "Priya Sharma", "9876543210"
BOOK = tools.ToolSpec("appointments", "book", "Book.", {"type": "object", "properties": {
    "name": {"type": "string"}, "phone": {"type": "string"}, "date": {"type": "string"}, "time": {"type": "string"}}})
ARGS = {"name": NAME, "phone": PHONE, "date": "2026-11-12", "time": "19:00"}


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("TOOLS_READBACK", "on")


@pytest.fixture()
def hub(monkeypatch):
    fake = FakeHub({"book": BOOKED})
    monkeypatch.setattr(tools, "hub", fake)
    return fake


def ok(text, **kw):
    return rb.slot_readback_ok(text, kw.get("d", D), kw.get("t", T), kw.get("name", NAME), kw.get("phone", PHONE))


# ---------------------------------------------------------------------------------------------- what counts as a read-back

GOOD = [
    "Thursday 12 November at 7 pm, for Priya Sharma, phone number ending 3210. Shall I book it?",
    "Thursday 12 November, 7:00 pm, Priya Sharma, number ending 3 2 1 0. Is that right?",
    "Shall I book Thursday 12 November at 19:00 for Priya Sharma, number ending 3210?",
    "So that is Thursday the 12th of November at 7 in the evening... sorry, 7 pm, for Priya Sharma, ending 3210. Shall I confirm?",
    "गुरुवार 12 नवंबर शाम 7 बजे, Priya Sharma के नाम से, फ़ोन नंबर के आख़िरी अंक 3210। क्या मैं बुक कर दूँ?",
    "गुरुवार १२ नवंबर, शाम सात बजे, Priya Sharma, नंबर ३२१०। बुक कर दूँ?",
    "Guruvaar 12 November, shaam 7 baje, Priya Sharma, number 3210. Book kar doon?",
    "गुरुवार 12 नोव्हेंबर संध्याकाळी 7 वाजता, Priya Sharma यांच्या नावाने, फोन नंबरचे शेवटचे अंक 3210. मी बुक करू का?",
    "Thursday 12 November, 7 pm, Priya Sharma, ending three two one zero. Shall I book it?",
]
BAD = [
    ("Thursday 12 November, 5:45 pm or 6:15 pm?", "an offer of two times"),
    ("Thursday 12 November at 7 pm or 7:30 pm for Priya Sharma 3210. Which one?", "two times"),
    ("I have 6:30 pm or 7 pm on Thursday 12 November. Which suits you?", "an offer, no name or phone"),
    ("Thursday 12 November at 7 pm. Shall I book it?", "no name, no phone"),
    ("Thursday 12 November at 7 pm for Priya Sharma. Shall I book it?", "no last digits of the phone"),
    ("Thursday 12 November at 7 pm for Priya Sharma, ending 3210, booking it now.", "no question"),
    ("Thursday at 7 pm for Priya Sharma, ending 3210. Shall I book it?", "no date number"),
    ("12 November at 7 pm for Anita Rao, ending 3210. Shall I book it?", "another name"),
    ("Friday 13 November at 7 pm for Priya Sharma, ending 3210. Shall I book it?", "another day"),
    ("Thursday or Friday 12 November at 7 pm, Priya Sharma, 3210. Shall I?", "two days"),
    ("Thursday 12 November at 7 am for Priya Sharma, ending 3210. Shall I book it?", "am, not pm"),
    ("Thursday 12 November at 7:15 pm for Priya Sharma, ending 3210. Shall I book it?", "other minutes"),
    ("Thursday 12 November at 7 pm for Priya Sharma, ending 9999. Shall I book it?", "other digits"),
    ("Shall I book it? Thursday 12 November at 7 pm for Priya Sharma, ending 3210.", "the question comes first"),
    ("", "empty"),
]


@pytest.mark.parametrize("text", GOOD)
def test_a_read_back_of_this_booking_counts(text):
    assert rb.missing(text, D, T, NAME, PHONE) == [], text


@pytest.mark.parametrize("text, why", BAD)
def test_anything_less_does_not_count(text, why):
    assert not ok(text), why


def test_the_hour_digit_alone_is_not_enough():
    # the old rule matched "6:15 pm" because "6" ... and the day: an offer of two times counted as a read-back of 6:15
    offer = "Thursday 12 November, 5:45 pm or 6:15 pm?"
    assert not rb.slot_readback_ok(offer, D, time(18, 15), NAME, PHONE)
    assert not rb.slot_readback_ok(offer, D, time(18, 15))              # even with no name or phone to check


def test_a_stay_names_its_nights():
    stay = "Thursday 12 November, for 2 nights, for Priya Sharma, ending 3210. Shall I book it?"
    assert rb.slot_readback_ok(stay, D, None, NAME, PHONE, 2)
    assert not rb.slot_readback_ok(stay, D, None, NAME, PHONE, 3)


def test_the_sentences_the_tool_gives_pass_the_rule_in_every_language():
    for args in (ARGS, {**ARGS, "time": "18:15", "name": "सुनीता कुलकर्णी"}, {**ARGS, "time": "09:30"}, {**ARGS, "time": "12:00"},
                 {**ARGS, "time": "00:45"}):
        said = rb.sentences(args)
        d, t = date.fromisoformat(args["date"]), time.fromisoformat(args["time"])
        for lang, sentence in said.items():
            assert rb.missing(sentence, d, t, args["name"], args["phone"]) == [], (lang, sentence)
            assert sentence.rstrip().endswith("?") and args["name"] in sentence


# ---------------------------------------------------------------------------------------------- the caller's yes

@pytest.mark.parametrize("said", ["haan", "Haan ji", "ha", "yes", "Yes please", "yeah ok", "theek hai", "thik hai", "chalega",
                                  "kar do", "haan kar do", "हाँ", "हां जी", "ठीक है", "बुक करो", "बुक कर दो", "हो", "होय",
                                  "ho", "ho chalel", "ठीक आहे", "7 pm is fine", "go ahead", "book it", "sounds good", "ji"])
def test_a_yes_is_a_yes(said):
    assert rb.is_affirmative(said, D, T), said


@pytest.mark.parametrize("said", ["no", "nahi", "nahin", "नहीं", "नको", "wait", "haan but make it 8 pm", "yes, Friday",
                                  "haan par 6 baje", "what time do you close", "is 8 pm free", "kal kar do", "hmm", "", "ok cancel",
                                  "no no not that", "नहीं रुको", "haan lekin dusra time", "yes my name is Anita and I want to change it"])
def test_a_no_a_change_or_a_new_request_is_not(said):
    import datetime as _dt
    day = D if D != _dt.date.today() + _dt.timedelta(days=1) else D + _dt.timedelta(days=7)   # "kal" must not be D itself
    assert not rb.is_affirmative(said, day, T), said


# ---------------------------------------------------------------------------------------------- in the tool loop

READ_BACK = rb.sentences(ARGS)["en"]


def test_offer_then_pick_gets_a_proper_read_back_first(hub, monkeypatch):
    monkeypatch.setenv("WHATSAPP_CONFIRMATIONS", "off")     # the WhatsApp clause has its own tests (test_call_flow_8nov.py)
    offer = "Thursday 6 pm is busy, but I can do 5:45 pm or 7 pm. Which suits you?"
    c = tools.execute(BOOK, ARGS, previous_agent_text=offer, caller_text="7 pm then, I am Priya Sharma, 9876543210")
    assert c.ok is False and c.value.startswith("NOT DONE") and hub.calls == []
    assert "Thursday 12 November at 7 pm, for Priya Sharma, phone number ending 3 2 1 0. Shall I book it?" in c.value
    assert "गुरुवार 12 नवंबर शाम 7 बजे" in c.value and "संध्याकाळी 7 वाजता" in c.value


def test_the_six_pm_seven_pm_case(hub):
    """"Is Thursday 6 pm free?" -> "6 pm is taken, 5:45 pm or 7 pm?" -> "7 pm then" -> book runs only after a read-back of 7 pm."""
    call_for_six = {**ARGS, "time": "18:00"}
    wrong = "Thursday 12 November at 6 pm for Priya Sharma, ending 3210. Shall I book it?"       # reads back 6, books 7
    c = tools.execute(BOOK, ARGS, previous_agent_text=wrong, caller_text="haan")
    assert c.ok is False and hub.calls == []
    c = tools.execute(BOOK, call_for_six, previous_agent_text=wrong, caller_text="haan")
    assert c.ok is True and hub.calls == [("book", call_for_six)]


def test_it_runs_after_the_read_back_and_a_yes(hub):
    for yes in ("haan", "yes please", "हाँ", "होय"):
        hub.calls.clear()
        c = tools.execute(BOOK, ARGS, previous_agent_text=READ_BACK, caller_text=yes)
        assert c.ok is True and hub.calls, yes


def test_a_no_or_a_change_after_the_read_back_does_not_book(hub):
    for said in ("nahi", "no wait", "make it 8 pm", "haan par Friday", "what is the fee"):
        c = tools.execute(BOOK, ARGS, previous_agent_text=READ_BACK, caller_text=said)
        assert c.ok is False and c.value.startswith("NOT DONE"), said
        assert "they have not said yes" in c.value
    assert hub.calls == []


def test_without_the_callers_words_only_the_read_back_is_checked(hub):
    c = tools.execute(BOOK, ARGS, previous_agent_text=READ_BACK)
    assert c.ok is True


def test_the_switch_still_turns_the_rule_off(hub, monkeypatch):
    monkeypatch.setenv("TOOLS_READBACK", "off")
    assert tools.execute(BOOK, ARGS, previous_agent_text=None, caller_text="no").ok is True
