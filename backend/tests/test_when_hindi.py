"""Hindi, Marathi and romanised ways of saying the time: साढ़े / सवा / पौने / डेढ़ / ढाई and their Marathi forms, am/pm from the
time of day or the opening hours, and what is not a time (a phone number said in digits, a party size, "ek" alone)."""
from datetime import date, datetime, time

import pytest

from services import when

NOW = datetime(2026, 10, 22, 14, 0)
CLINIC = [(9 * 60, 13 * 60), (16 * 60, 20 * 60)]       # 9 to 1 and 4 to 8
OFFICE = [(9 * 60, 18 * 60)]

# (phrase, expected time, opening hours or None)
TIMES = [
    # साढ़े, half past: the hour is the one said, with 30 minutes
    ("साढ़े चार बजे", time(16, 30), None),
    ("शाम साढ़े चार बजे", time(16, 30), None),
    ("सुबह साढ़े चार बजे", time(4, 30), None),
    ("दोपहर साढ़े चार बजे", time(16, 30), None),
    ("साढ़े नौ बजे", time(9, 30), None),
    ("सुबह साढ़े नौ बजे", time(9, 30), None),
    ("रात साढ़े नौ बजे", time(21, 30), None),
    ("साढ़ेचार बजे", time(16, 30), None),
    ("saade char baje", time(16, 30), None),
    ("saadhe paanch baje", time(17, 30), None),
    ("sadhe nau baje", time(9, 30), None),
    ("half past four", time(16, 30), None),
    # सवा, quarter past
    ("सवा चार बजे", time(16, 15), None),
    ("सवा दस बजे", time(10, 15), None),
    ("sawa char baje", time(16, 15), None),
    ("sava das baje", time(10, 15), None),
    ("quarter past nine", time(9, 15), None),
    # पौने, quarter to
    ("पौने पांच बजे", time(16, 45), None),
    ("पौने दस बजे", time(9, 45), None),
    ("paune paanch baje", time(16, 45), None),
    ("pone das baje", time(9, 45), None),
    ("quarter to five", time(16, 45), None),
    ("पौने एक बजे", time(12, 45), None),
    # डेढ़, ढाई
    ("डेढ़ बजे", time(13, 30), None),
    ("दोपहर डेढ़ बजे", time(13, 30), None),
    ("ढाई बजे", time(14, 30), None),
    ("शाम ढाई बजे", time(14, 30), None),
    ("dedh baje", time(13, 30), None),
    ("dhai baje", time(14, 30), None),
    # Marathi
    ("साडे चार वाजता", time(16, 30), None),
    ("साडेचार वाजता", time(16, 30), None),
    ("सकाळी साडे नऊ वाजता", time(9, 30), None),
    ("सव्वा चार वाजता", time(16, 15), None),
    ("सव्वा दहा वाजता", time(10, 15), None),
    ("पावणे पाच वाजता", time(16, 45), None),
    ("पावणे दहा वाजता", time(9, 45), None),
    ("दीड वाजता", time(13, 30), None),
    ("अडीच वाजता", time(14, 30), None),
    ("संध्याकाळी साडे सहा वाजता", time(18, 30), None),
    ("दुपारी दीड वाजता", time(13, 30), None),
    # am/pm from the time of day
    ("सुबह 10 बजे", time(10, 0), None),
    ("शाम 5 बजे", time(17, 0), None),
    ("दोपहर 2 बजे", time(14, 0), None),
    ("दोपहर 12 बजे", time(12, 0), None),
    ("रात 9 बजे", time(21, 0), None),
    ("सकाळी 8 वाजता", time(8, 0), None),
    ("संध्याकाळी 7 वाजता", time(19, 0), None),
    # am/pm from the opening hours when the caller did not say
    ("4:30", time(16, 30), CLINIC),
    ("4:30", time(16, 30), OFFICE),
    ("9:30", time(9, 30), CLINIC),
    ("9 baje", time(9, 0), CLINIC),
    ("साढ़े चार बजे", time(16, 30), CLINIC),
    ("साढ़े नौ बजे", time(9, 30), CLINIC),
    ("5 baje", time(17, 0), CLINIC),
    ("sawa paanch baje", time(17, 15), OFFICE[:0] or CLINIC),
    ("10 baje", time(10, 0), CLINIC),
    ("11:15", time(11, 15), OFFICE),
    ("8 baje", time(8, 0), None),
    ("3 baje", time(15, 0), None),
    # an explicit word beats the opening hours
    ("सुबह साढ़े चार बजे", time(4, 30), OFFICE),
    ("4:30 pm", time(16, 30), None),
    ("4:30 am", time(4, 30), None),
    # not a time
    ("9876543210", None, None),
    ("98450 12345", None, None),
    ("nau aath chaar paanch zero ek do teen char paanch", None, None),
    ("नौ आठ चार पांच शून्य एक दो तीन चार पांच", None, None),
    ("my number is nine eight four five zero one two three four five", None, None),
    ("चार लोग", None, None),
    ("char log", None, None),
    ("for four people", None, None),
    ("table for four people", None, None),
    ("paanch log", None, None),
    ("तीन जण", None, None),
    ("ek", None, None),
    ("one", None, None),
    ("do", None, None),
    ("I am Priya", None, None),
    # a time beside a party size or a phone number is still the time
    ("for four people at 5 pm", time(17, 0), None),
    ("चार लोग शाम 6 बजे", time(18, 0), None),
    ("char log saade saat baje", time(19, 30), None),
    ("98450 12345 5 baje", time(17, 0), None),
    ("I am coming at 5 pm", time(17, 0), None),
    ("ek baje", time(13, 0), None),
]


@pytest.mark.parametrize("phrase, expected, opening", TIMES)
def test_the_time_a_phrase_names(phrase, expected, opening):
    assert when.resolve_time(phrase, opening).time == expected, phrase


def test_there_are_forty_phrases_or_more():
    assert len(TIMES) >= 40


def test_the_opening_hours_decide_and_the_note_says_so():
    r = when.resolve_time("4:30", CLINIC)
    assert r.time == time(16, 30) and r.assumed_meridiem and any("opening hours" in n for n in r.notes)
    # both readings inside the hours: the usual rule (1 to 7 is the evening)
    r = when.resolve_time("5:00", [(4 * 60, 23 * 60)])
    assert r.time == time(17, 0) and r.assumed_meridiem
    # neither inside: the usual rule too
    assert when.resolve_time("6 baje", [(9 * 60, 10 * 60)]).time == time(18, 0)


def test_opening_hours_can_be_read_from_a_schedule():
    schedule = {"week": {"mon": [{"start": "09:00", "end": "13:00"}, {"start": "16:00", "end": "20:00"}], "tue": []}}
    monday = date(2026, 10, 26)
    assert when.opening_ranges(schedule, monday) == [(540, 780), (960, 1200)]
    assert when.opening_ranges(schedule, date(2026, 10, 27)) == []
    assert when.opening_ranges(schedule) == [(540, 780), (960, 1200)]
    assert when.opening_ranges(None) == []


def test_the_request_reader_and_the_tool_arguments_use_the_opening_hours():
    early = [(7 * 60, 11 * 60)]          # a morning-only desk: "6" would be 6 am if it opened then; here 8:30 is the one
    r = when.resolve_request("kal साढ़े आठ बजे", NOW, lambda d: early)
    assert (r.date, r.time) == (date(2026, 10, 23), time(8, 30))
    args, notes = when.normalise_tool_args({"date": "kal", "time": "4.30"}, NOW, CLINIC)
    assert args == {"date": "2026-10-23", "time": "16:30"}
    args, _ = when.normalise_tool_args({"date": "2026-11-12", "time": "साढ़े चार बजे"}, NOW, CLINIC)
    assert args["time"] == "16:30"


def test_a_party_size_in_a_request_is_not_the_hour():
    r = when.resolve_request("table for four people tomorrow", NOW)
    assert r.time is None and r.party == 4 and r.date == date(2026, 10, 23)
    r = when.resolve_request("कल चार लोग शाम 7 बजे", NOW)
    assert (r.date, r.time, r.party) == (date(2026, 10, 23), time(19, 0), 4)
