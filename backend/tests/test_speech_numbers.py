"""Digits in a Hindi reply are rewritten for the Hindi voice (services/speech_numbers.py): a voice that gets "1,60,000"
reads it split at its commas, skips "₹" and reads a phone number as one big amount. The grounding prompt now asks for digits in every language, so this matters on every Hindi call."""
import asyncio

import pytest

from services import speech_numbers as sn
from services.speech_numbers import hindi_numbers_for_speech as speak
from tests.test_offline_agents import env  # noqa: F401  (a fixture)


@pytest.mark.parametrize("text, said", [
    # Indian and Western digit grouping, with and without a currency mark
    ("MBA की सालाना फीस ₹1,60,000 है।", "MBA की सालाना फीस 160000 रुपये है।"),
    ("फीस 2,50,00,000 रुपये", "फीस 25000000 रुपये"),
    ("कुल 1,500 सीटें", "कुल 1500 सीटें"),
    ("फीस Rs. 500 है, फॉलो-अप Rs 1,500/- रुपये।", "फीस 500 रुपये है, फॉलो-अप 1500 रुपये।"),
    ("ICU का किराया ₹9,000 प्रति दिन", "ICU का किराया 9000 रुपये प्रति दिन"),
    ("₹2500000 का पैकेज", "2500000 रुपये का पैकेज"),        # an amount without commas is not a phone number
    ("फीस ₹१,६०,००० है", "फीस 160000 रुपये है"),             # Devanagari digits
    # phone numbers: digit by digit
    ("हेल्पलाइन 020 5550 1210 है।", "हेल्पलाइन 0 2 0, 5 5 5 0, 1 2 1 0 है।"),
    ("कॉल करें +91 98765 43210 पर", "कॉल करें 9 8 7 6 5, 4 3 2 1 0 पर"),
    ("नंबर 9876543210 है", "नंबर 9 8 7 6 5, 4 3 2 1 0 है"),
    ("500 - 1000 रुपये", "500 - 1000 रुपये"),                # a range is not a phone number
    # clock times as they are said
    ("ओपीडी 9:30 AM से 1:00 PM तक खुली है।", "ओपीडी सुबह साढ़े 9 बजे से दोपहर 1 बजे तक खुली है।"),
    ("शाम 16:00 बजे से", "शाम 4 बजे से"),
    ("कार्डियोलॉजी 9 AM से 1 PM", "कार्डियोलॉजी सुबह 9 बजे से दोपहर 1 बजे"),
    ("12:45 PM", "दोपहर पौने 1 बजे"),
    ("1:30 PM", "दोपहर डेढ़ बजे"),
    ("4:10 PM", "शाम 4 बजकर 10 मिनट"),
    # decimals and percentages
    ("फीस 1.5 लाख है", "फीस डेढ़ लाख है"),
    ("2.5 लाख", "ढाई लाख"),
    ("20% छूट", "20 प्रतिशत छूट"),
    ("ग्रेड 7.25", "ग्रेड 7 दशमलव 2 5"),
    # left alone
    ("B.Tech Mechanical Engineering में कुल 60 सीटें उपलब्ध हैं।", "B.Tech Mechanical Engineering में कुल 60 सीटें उपलब्ध हैं।"),
    ("नमस्ते, मैं आपकी क्या मदद कर सकती हूँ?", "नमस्ते, मैं आपकी क्या मदद कर सकती हूँ?"),
    ("", ""),
])
def test_hindi_numbers_for_speech(text, said):
    assert speak(text) == said


def test_turn_spoken_rewrites_numbers_for_hindi_only(env):
    hi, lang = asyncio.run(env.turn_spoken("hi", "फीस ₹1,60,000 है।", False))
    assert lang == "hi" and hi == "फीस 160000 रुपये है।"
    en, lang = asyncio.run(env.turn_spoken("en", "The fee is ₹1,60,000.", False))
    assert lang == "en" and en == "The fee is ₹1,60,000."
    # Hinglish is spoken by the Hindi voice from Devanagari: its numbers are rewritten too
    hg, lang = asyncio.run(env.turn_spoken("hinglish", "Fees ₹1,60,000 hai.", False, use_llm=False))
    assert lang == "hi" and "160000 रुपये" in hg


# ------------------------------------------------------------------------------------------- phone numbers in groups

@pytest.mark.parametrize("text, said", [
    ("Your number is 98765 43210, right?", "Your number is 9 8 7 6 5, 4 3 2 1 0, right?"),
    ("Call +91 9876543210 any time.", "Call 9 8 7 6 5, 4 3 2 1 0 any time."),
    ("तुमचा नंबर 98765-43210 आहे.", "तुमचा नंबर 9 8 7 6 5, 4 3 2 1 0 आहे."),
    ("The helpline is 020 5550 1210.", "The helpline is 0 2 0, 5 5 5 0, 1 2 1 0."),
    ("Already grouped: 9 8 7 6 5, 4 3 2 1 0.", "Already grouped: 9 8 7 6 5, 4 3 2 1 0."),
    ("Fees Rs 2500000, booked on 2026-11-12 at 7 pm.", "Fees Rs 2500000, booked on 2026-11-12 at 7 pm."),
    ("Room 40512 is free.", "Room 40512 is free."),
])
def test_phone_numbers_are_said_in_groups_in_any_language(text, said):
    assert sn.phone_numbers_for_speech(text) == said


def test_a_mobile_number_is_grouped_five_and_five_with_the_prefix_on_its_own():
    assert sn.spoken_phone("9876543210") == "9 8 7 6 5, 4 3 2 1 0"
    assert sn.spoken_phone("09876543210") == "0, 9 8 7 6 5, 4 3 2 1 0"
    assert sn.spoken_phone("919876543210") == "9 1, 9 8 7 6 5, 4 3 2 1 0"
    assert sn.phone_groups("12345678") == ["1234", "5678"] and sn.phone_groups("123456789") == ["1234", "56789"]
