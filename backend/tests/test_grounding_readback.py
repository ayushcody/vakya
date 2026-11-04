"""The answer guard (services/grounding.py) lets the agent read back what the caller gave (a phone number in groups or digit
by digit, its last four digits, the time and the date of a booking) and still stops a number nothing supports."""
import types

import pytest

from services import grounding as g


def blocked(reply, *sources, tool_results=(), agent=None):
    messages = [{"role": "system", "content": "You are a receptionist."}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": t} for i, t in enumerate(sources)]
    calls = [types.SimpleNamespace(arguments=a, result=r, value=r) for a, r in tool_results]
    guard = g.AnswerGuard(messages, agent or {}, tool_calls=lambda: calls)
    return [v.text for v in guard.check(reply)]


@pytest.mark.parametrize("reply, said", [
    ("Your number is 98450 12345.", "my number is 9845012345"),
    ("Your number is 98450 12345.", "my number is 98450 12345"),
    ("Your number is 98450-12345.", "9845012345"),
    ("Your number is 98450 12345.", "nine eight four five zero one two three four five"),
    ("Your number is 98450 12345.", "नौ आठ चार पाँच शून्य एक दो तीन चार पाँच"),
    ("Your number is 98450 12345.", "मेरा नंबर ९८४५० १२३४५ है"),
    ("Your number is 9 8 4 5 0 1 2 3 4 5.", "my number is 9845012345"),
    ("Number ending 3210, right?", "my number is 9876543210"),
    ("Number ending 3 2 1 0, right?", "my number is 9876543210"),
    ("फ़ोन नंबर के आख़िरी अंक 3210", "mera number 98765 43210 hai"),
    ("+91 98450 12345", "9845012345"),
])
def test_a_phone_number_the_caller_gave_may_be_read_back(reply, said):
    assert blocked(reply, said) == []


@pytest.mark.parametrize("reply, said", [
    ("Call us on 98450 12345.", "what are your timings"),
    ("Call us on 9 8 4 5 0 1 2 3 4 5.", "what are your timings"),
    ("Your number is 98450 12345.", "my number is 98760 54321"),
    ("Number ending 9999, right?", "my number is 9876543210"),
    ("Number ending 9 9 9 9, right?", "my number is 9876543210"),
])
def test_a_number_nothing_supports_is_still_blocked(reply, said):
    assert blocked(reply, said), reply


def test_the_time_and_the_date_of_the_booking_may_be_read_back():
    args = {"date": "2026-11-12", "time": "19:00", "name": "Priya Sharma", "phone": "9876543210"}
    reply = "Thursday 12 November at 7 pm, for Priya Sharma, phone number ending 3 2 1 0. Shall I book it?"
    assert blocked(reply, "Is Thursday 6 pm free?", "7 pm then, I am Priya Sharma, 98765 43210", tool_results=[(args, "NOT DONE")]) == []
    # a time or day nobody mentioned is still caught
    bad = blocked("Thursday 13 November at 8 pm. Shall I book it?", "Is Thursday 6 pm free?", "7 pm then",
                  tool_results=[(args, "NOT DONE")])
    assert "13" in bad and "8" in bad


def test_fees_and_hours_the_documents_do_not_have_are_still_blocked():
    assert blocked("The fee is Rs. 500.", "what is the fee?", agent={"system_instructions": "Fee Rs. 350."}) == ["500"]
    assert blocked("We open at 9 am.", "when do you open?", agent={"system_instructions": "Open 10 am to 5 pm."})
