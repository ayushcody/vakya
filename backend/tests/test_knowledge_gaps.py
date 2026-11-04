"""Gap detection (is_gap_reply, English, Hindi, Hinglish) and redaction of identifiers (services/knowledge_gaps.py)."""
import pytest

from services import knowledge_gaps as kg

NO_INFO = "I'm sorry, I don't have that information. I can connect you with a member of staff."


# -- detection ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("reply", [
    NO_INFO,
    "Sorry, I do not have that information right now.",
    "I’m not sure about that, let me get a member of staff.",  # curly apostrophe
    "माफ कीजिए, मेरे पास यह जानकारी नहीं है।",  # Hindi
    "मुझे नहीं पता, मैं स्टाफ से पूछता हूं।",
    "Maaf kijiye, mere paas yeh jaankari nahi hai.",  # Hinglish
    "Mujhe nahi pata, main staff se baat karwata hoon.",
    "Lo siento, no tengo esa información.",
])
def test_gap_replies_are_detected(reply):
    assert kg.is_gap_reply(reply)


@pytest.mark.parametrize("reply", [
    "", "Refunds are possible within thirty days.", "We are open on Sundays from 10 to 1.",
    "रिफंड तीस दिन में मिल जाएगा।",
    "Aapka refund teen din mein aa jayega.",
])
def test_answers_are_not_gaps(reply):
    assert not kg.is_gap_reply(reply)


# -- redaction ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,gone", [
    ("call me on 9876543210 about fees", "9876543210"),
    ("my number is +91 98765 43210 please", "98765"),
    ("+91-9876543210 is mine", "9876543210"),
    ("Aadhaar 1234 5678 9012 is linked", "5678"),
    ("aadhaar 123456789012 hai", "123456789012"),
    ("mail ayush.c@example.co.in now", "example"),
    ("card 4111 1111 1111 1111 declined", "4111"),
    ("order 55512345 status", "55512345"),
])
def test_redact_removes_identifiers(raw, gone):
    out = kg.redact(raw)
    assert gone not in out and "[" in out


def test_redact_keeps_ordinary_numbers_and_truncates():
    assert kg.redact("Is it open on 15 August 2026 at 10:30?") == "Is it open on 15 August 2026 at 10:30?"
    assert len(kg.redact("word " * 500)) <= kg.MAX_TEXT_CHARS


