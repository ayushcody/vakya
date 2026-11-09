"""The claim guard (services/tools.py claims_unconfirmed_write): a reply that says a booking, request or cancellation
was made on a day or at a time, while no write tool succeeded this turn, is replaced by the safe line. Questions,
offers, refusals and claims backed by a successful tool call are left alone."""
import pytest

from services import llm, tools
from tests.test_tools_client import BOOK, BOOKED, FakeHub, MESSAGES, call, native, scripted_chat  # noqa: F401

OK_BOOK = tools.ToolCall("book_appointment", {}, True, "booking A10", 1, BOOKED[1])
FAILED_BOOK = tools.ToolCall("book_appointment", {}, False, "failed: NOT DONE", 1, "NOT DONE")
OK_READ = tools.ToolCall("find_booking", {}, True, "1 booking", 1, {"bookings": []})

CLAIMS = [
    "मैं आपकी बुकिंग को 2026-10-23, 7:00 AM के लिए पुष्टि कर देता हूँ।",          # the local 3B reply of 22 Oct
    "Aapka appointment kal subah 11 baje book ho gaya hai.",
    "Your appointment is booked for Monday 26 October at 11:00.",
    "Done, you are booked for tomorrow at 4 pm.",
    "I have reserved the 10:30 slot for you.",
    "आपका अपॉइंटमेंट सोमवार 11 बजे बुक हो गया है।",
    "Your meeting request has been sent for Tuesday 27 October at 15:30.",
    "Booking A12 cancelled for Friday.",                                            # a cancellation claim
    "Theek hai, maine Monday 11 baje ka slot lock kar diya hai.",
]

NOT_CLAIMS = [
    "Monday 26 October 2026 at 11:00 (11 am) for Priya Sharma. Shall I book it?",   # the read-back question
    "Kya main kal 11 baje book kar doon?",                                           # an offer
    "सोमवार 26 अक्टूबर, सुबह 11 बजे। क्या मैं बुक कर दूँ?",
    "Sorry, I cannot book for Sunday evening; the clinic is closed.",                # a refusal
    "Main Sunday ko book nahi kar sakti, clinic band hai.",
    "The clinic is open on Monday from 9 am to 1 pm.",                               # a plain fact with a time
    "Your appointment is booked.",                                                   # no day or time: left to the id guard
    "Which day would you like, Monday or Tuesday?",
    "I could not confirm that with the booking system.",
]


@pytest.mark.parametrize("text", CLAIMS)
def test_a_claimed_write_with_a_day_or_time_and_no_tool_is_caught(text):
    assert tools.claims_unconfirmed_write(text, []) is not None, text
    assert tools.guard_sentence(text, []) == tools.UNCONFIRMED_LINE, text
    assert tools.guard_sentence(text, [FAILED_BOOK]) == tools.UNCONFIRMED_LINE, text   # a refused call is no cover
    assert tools.guard_sentence(text, [OK_READ]) == tools.UNCONFIRMED_LINE, text       # nor a read-only one


@pytest.mark.parametrize("text", NOT_CLAIMS)
def test_questions_offers_refusals_and_plain_facts_pass(text):
    assert tools.claims_unconfirmed_write(text, []) is None, text
    assert tools.guard_sentence(text, []) is None, text


def test_a_claim_backed_by_a_successful_write_passes():
    for text in CLAIMS:
        assert tools.claims_unconfirmed_write(text, [OK_BOOK]) is None, text
    ok_cancel = tools.ToolCall("cancel_appointment", {}, True, "cancelled A12", 1, {"booking_id": "A12"})
    assert tools.guard_sentence("Booking A12 cancelled for Friday.", [ok_cancel]) is None


def test_the_offending_sentence_is_named_even_inside_a_longer_reply():
    text = "Namaste! Aapka appointment kal 11 baje book ho gaya hai. Kuch aur chahiye?"
    assert tools.claims_unconfirmed_write(text, []) == "Aapka appointment kal 11 baje book ho gaya hai."


def test_the_turn_loop_replaces_a_claimed_booking(monkeypatch, native):
    monkeypatch.setattr(tools, "hub", FakeHub())
    scripted_chat(monkeypatch, "Great, you are booked for Monday at 11 am. See you then!")
    assert tools.run_tool_turn(MESSAGES, [BOOK]).text == tools.UNCONFIRMED_LINE
    # the same words after a real booking are fine
    monkeypatch.setattr(tools, "hub", FakeHub({"book_appointment": BOOKED}))
    scripted_chat(monkeypatch, [call("book_appointment", name="P", phone="9876543210")],
                  "Great, you are booked for Monday at 10:30. See you then!")
    assert tools.run_tool_turn(MESSAGES, [BOOK]).text == "Great, you are booked for Monday at 10:30. See you then!"


def test_restating_a_booking_made_earlier_in_the_call_is_not_a_new_claim():
    """"thank you, bye" after a booking: the agent may say again that it is booked (an id from a tool was said earlier)."""
    text = "Your Wednesday 11 November 5 PM appointment is booked. Thank you, goodbye!"
    assert tools.guard_sentence(text.split(". ")[0] + ".", [], "Booking ID A30 for Wednesday 11 November at 5 PM") is None
    assert tools.guard_sentence(text.split(". ")[0] + ".", [], "Shall I book it? yes") == tools.UNCONFIRMED_LINE   # no id said: caught


def test_bracketed_tool_narration_is_never_spoken():
    from services.turn_stream import strip_directions
    assert strip_directions("[Calling book now with the caller's details] बुधवार 11 नवंबर शाम 5 बजे. क्या मैं बुक कर दूँ?") \
        == "बुधवार 11 नवंबर शाम 5 बजे. क्या मैं बुक कर दूँ?"
    assert strip_directions("[Calling the booking tool]") == ""
    assert strip_directions("Dr. Kulkarni sees patients on Tuesdays.") == "Dr. Kulkarni sees patients on Tuesdays."


def test_a_pending_readback_is_added_when_the_reply_lacks_it():
    args = {"date": "2026-11-11", "time": "17:00", "name": "Ayush Chaugule", "phone": "8788996295"}
    pending = tools.ToolCall("book", args, False, "failed: NOT DONE: first read this back to the caller; x", 1)
    line = tools.pending_readback_line([pending], "hi", "अब मैं आपकी बुकिंग कर देती हूँ।")
    assert line and line.rstrip().endswith("?") and "6 2 9 5" in line
    assert tools.pending_readback_line([pending], "hi", line) is None            # already said
    assert tools.pending_readback_line([OK_BOOK], "hi", "x") is None             # nothing pending
    assert tools.pending_readback_line([pending], "en", "ok").endswith("?")
