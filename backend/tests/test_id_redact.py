"""Identity numbers a caller says are removed from what is stored; phone numbers stay (masked for display as before)."""
import pytest

from services import call_events, id_redact
from services.id_redact import MARKER, redact_ids


@pytest.mark.parametrize("said", [
    "my aadhaar is 2345 6789 0123 please",
    "my aadhaar is 234567890123 please",
    "my aadhaar is 2345-6789-0123 please",
    "mera aadhaar 2 3 4 5 6 7 8 9 0 1 2 3 hai",
    "mera aadhaar two three four five six seven eight nine zero one two three hai",
    "mera aadhaar दो तीन चार पाँच छह सात आठ नौ शून्य एक दो तीन hai",
    "आधार नंबर २३४५ ६७८९ ०१२३ है",
    "aadhaar double two three four five six seven eight nine zero one two",
    "aadhaar 2345, 6789, 0123",
])
def test_a_twelve_digit_number_is_removed_however_it_is_said(said):
    out = redact_ids(said)
    assert MARKER in out
    for leftover in ("2345", "6789", "0123", "234567890123", "२३४५", "two three four five", "तीन चार"):
        assert leftover not in out


@pytest.mark.parametrize("said", ["my pan is ABCDE1234F", "pan abcde1234f ok", "PAN ABCDE 1234 F", "pan A B C D E 1 2 3 4 F"])
def test_a_pan_is_removed(said):
    out = redact_ids(said)
    assert MARKER in out and "1234" not in out and "1 2 3 4" not in out


@pytest.mark.parametrize("card", ["4111 1111 1111 1111", "4111111111111111", "5500-0000-0000-0004", "378282246310005",
                                  "6011 0000 0000 0004", "4222222222222"])
def test_card_numbers_that_pass_luhn_are_removed(card):
    out = redact_ids(f"card number {card} expiry")
    assert out == f"card number {MARKER} expiry"


def test_a_long_run_that_fails_luhn_is_not_taken_for_a_card():
    assert MARKER not in redact_ids("order 4111 1111 1111 1112")


@pytest.mark.parametrize("said", ["my OTP is 482913", "otp 4829", "OTP: 4 8 2 9 1 3", "my otp is four eight two nine one three",
                                  "ओटीपी 482913 है", "one time password is 482913", "OTP hai 48 29 13"])
def test_the_digits_after_otp_are_removed(said):
    out = redact_ids(said)
    assert MARKER in out
    assert not any(ch.isdigit() for ch in out.replace(MARKER, ""))
    assert "four eight" not in out


@pytest.mark.parametrize("said", [
    "my number is 98450 12345", "9845012345", "+91 98450 12345", "+919845012345", "091 98450 12345", "nine eight four five zero one two three four five",
    "book for 4 people at 6 pm on 12 November 2026", "my otp was fine, come at 4 30", "ek do teen char", "I am 34 years old, 2 kids",
    "date 2026-11-12", "room 4012 for 2 nights",
])
def test_phone_numbers_and_ordinary_numbers_are_kept(said):
    assert redact_ids(said) == said


def test_a_phone_number_beside_an_id_survives():
    out = redact_ids("call me on 98450 12345 and my aadhaar is 2345 6789 0123.")
    assert "98450 12345" in out and out.endswith(f"is {MARKER}.")


def test_the_phone_number_is_still_masked_for_display():
    assert call_events.mask_numbers(redact_ids("98450 12345")) == "••••2345"


def test_the_reply_that_repeats_a_number_is_cleaned_too_and_nothing_else_changes():
    assert redact_ids("Sorry, I cannot take 2345 6789 0123 on the phone.") == f"Sorry, I cannot take {MARKER} on the phone."
    assert redact_ids("") == "" and redact_ids(None) is None
    assert id_redact.has_ids("aadhaar 234567890123") and not id_redact.has_ids("hello")
