"""Opening hours from words (services/hours_parser.py and POST /api/schedules/parse-hours): the rules, the notes, the
fallback to the language model, and the validation of whatever comes back."""
from datetime import date

import pytest

from services import hours_parser as hp, llm

LOCAL = {"storage-mode": "local"}
TODAY = date(2026, 11, 3)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def parse(text, **kw):
    return hp.parse(text, TODAY, use_model=False, **kw)


def hours(week):
    """{day: "10:00-13:00,17:00-21:00"} for the open days, to compare briefly."""
    return {d: ",".join(f"{r['start']}-{r['end']}" for r in week[d]) for d in DAYS if week[d]}


def same(days, text):
    return {d: text for d in days}


@pytest.mark.parametrize("text,expected", [
    # English
    ("Mon to Sat 10 to 1 and 5 to 9, Sunday closed", same(DAYS[:6], "10:00-13:00,17:00-21:00")),
    ("Monday to Friday 9 to 5", same(DAYS[:5], "09:00-17:00")),
    ("Mon-Fri 9am-5pm", same(DAYS[:5], "09:00-17:00")),
    ("Mon - Sat 10:00 to 18:30", same(DAYS[:6], "10:00-18:30")),
    ("daily 6 am to 10 am and 5 pm to 9 pm", same(DAYS, "06:00-10:00,17:00-21:00")),
    ("every day 8 to 8", same(DAYS, "08:00-20:00")),
    ("open all days 10 to 6", same(DAYS, "10:00-18:00")),
    ("weekdays 9 to 5, Sat 10 to 2", {**same(DAYS[:5], "09:00-17:00"), "sat": "10:00-14:00"}),
    ("weekends 10 to 4", {"sat": "10:00-16:00", "sun": "10:00-16:00"}),
    ("Mon, Wed and Fri 10 to 1", {"mon": "10:00-13:00", "wed": "10:00-13:00", "fri": "10:00-13:00"}),
    ("Tuesday to Sunday 10 am to 8 pm", same(DAYS[1:], "10:00-20:00")),
    ("Fri to Mon 10 to 2", {"fri": "10:00-14:00", "sat": "10:00-14:00", "sun": "10:00-14:00", "mon": "10:00-14:00"}),
    ("all days except Sunday 10am-6pm", same(DAYS[:6], "10:00-18:00")),
    ("10:00 to 13:00 and 17:00 to 20:00", same(DAYS, "10:00-13:00,17:00-20:00")),
    ("24 hours", same(DAYS, "00:00-24:00")),
    ("Sat 24x7, Mon to Fri 9 to 6", {**same(DAYS[:5], "09:00-18:00"), "sat": "00:00-24:00"}),
    ("between 10 and 2 on weekdays", same(DAYS[:5], "10:00-14:00")),
    ("Mon to Sat 12 to 3 and 7 to 11 pm", same(DAYS[:6], "12:00-15:00,19:00-23:00")),
    ("Mon to Sat 9.30 to 5.30", same(DAYS[:6], "09:30-17:30")),
    # Hindi
    ("सोमवार से शनिवार सुबह 10 से 2", same(DAYS[:6], "10:00-14:00")),
    ("सोमवार से शुक्रवार 10 बजे से 5 बजे तक", same(DAYS[:5], "10:00-17:00")),
    ("रोज़ सुबह 6 से 10 और शाम 5 से 9", same(DAYS, "06:00-10:00,17:00-21:00")),
    ("रविवार बंद, बाकी दिन 10 से 6", None),   # "बाकी दिन" is not understood: see the note test below
    ("सोमवार से शनिवार 10 से 1 और 5 से 9, रविवार छुट्टी", same(DAYS[:6], "10:00-13:00,17:00-21:00")),
    ("das se do", same(DAYS, "10:00-14:00")),
    ("somvaar se shanivaar subah 10 se 2", same(DAYS[:6], "10:00-14:00")),
    ("mangalvaar se ravivaar 10 baje se 8 baje tak", same(DAYS[1:], "10:00-20:00")),
    # Marathi
    ("सोमवार ते शुक्रवार सकाळी 10 ते 2, रविवार बंद", same(DAYS[:5], "10:00-14:00")),
    ("सोमवार ते शनिवार सकाळी 9 ते 1 आणि संध्याकाळी 5 ते 8", same(DAYS[:6], "09:00-13:00,17:00-20:00")),
])
def test_what_it_reads(text, expected):
    got = parse(text)
    if expected is None:
        return
    assert hours(got["week"]) == expected, got
    assert got["source"] == "parser"


@pytest.mark.parametrize("text", ["Mon to Sat 10 to 1 and 5 to 9, Sunday closed", "सोमवार से शनिवार सुबह 10 से 2",
                                  "Mon-Fri 9 to 5", "weekdays 10am to 6pm", "daily 9 to 9"])
def test_the_ordinary_cases_are_confident(text):
    got = parse(text)
    assert got["confident"] is True and got["notes"] == [], got


def test_closed_days_come_before_or_after_the_word():
    a = parse("Mon to Sat 10 to 6, closed Sunday")
    b = parse("Mon to Sat 10 to 6, Sunday closed")
    c = parse("Mon to Sat 10 to 6, Sunday off")
    d = parse("Mon to Sat 10 to 6, रविवार बंद")
    assert a["week"] == b["week"] == c["week"] == d["week"] and a["week"]["sun"] == []
    only = parse("Sunday closed")
    assert only["confident"] is True and all(not v for v in only["week"].values()) and only["source"] == "parser"
    later = parse("closed on Sundays and Mondays, Tue to Sat 10 to 6")
    assert hours(later["week"]) == same(DAYS[1:6], "10:00-18:00")


def test_a_later_group_overrides_an_earlier_one():
    got = parse("daily 9 to 5, Saturday 10 to 2, Sunday closed")
    assert hours(got["week"]) == {**same(DAYS[:5], "09:00-17:00"), "sat": "10:00-14:00"}


def test_breaks_cut_the_ranges():
    for text in ("Mon-Fri 10 to 6 with a 1 to 2 break", "Mon-Fri 10 to 6, lunch 1 to 2", "Mon-Fri 10 to 6, break 13:00 to 14:00",
                 "Mon-Fri 10 to 6 and lunch break 1 to 2", "Mon to Fri 10 to 6, लंच 1 से 2"):
        got = parse(text)
        assert hours(got["week"]) == same(DAYS[:5], "10:00-13:00,14:00-18:00"), (text, got)
    ahead = parse("Mon to Fri lunch 1 to 2, 10 to 6")
    assert hours(ahead["week"]) == same(DAYS[:5], "10:00-13:00,14:00-18:00")
    last = parse("Mon-Fri 9 to 5, Sat 10 to 2, lunch 1 to 2")           # a break belongs to the group it follows
    assert hours(last["week"])["mon"] == "09:00-17:00" and hours(last["week"])["sat"] == "10:00-13:00"
    every = parse("Mon-Fri 9 to 5, Sunday closed, lunch 1 to 2")        # after a closed day it applies to every open day
    assert hours(every["week"]) == same(DAYS[:5], "09:00-13:00,14:00-17:00")


def test_the_slot_length_and_closed_dates():
    got = parse("Mon to Sat 10 to 6, every 15 minutes, closed on 25 December and 1 Jan")
    assert got["slot_minutes"] == 15 and got["closed_dates"] == ["2026-12-25", "2027-01-01"]
    assert parse("30 min slots, weekdays 9 to 5")["slot_minutes"] == 30
    assert parse("hourly, Mon to Fri 9 to 5")["slot_minutes"] == 60
    assert parse("हर 20 मिनट, सोमवार से शुक्रवार 10 से 5")["slot_minutes"] == 20
    assert parse("Mon to Fri 9 to 5, 2 min slots")["slot_minutes"] is None
    assert any("2 minutes" in n for n in parse("Mon to Fri 9 to 5, 2 min slots")["notes"])
    only = parse("closed on 15 November")
    assert only["closed_dates"] == ["2026-11-15"] and only["source"] == "parser"


def test_times_without_am_or_pm_are_inferred_and_a_guess_is_flagged():
    got = parse("Mon to Sat 6 to 10")
    assert hours(got["week"])["mon"] == "18:00-22:00"
    assert got["confident"] is False and any("6 to 10" in n for n in got["notes"])
    assert parse("Mon to Sat 6 am to 10 am")["confident"] is True and \
        hours(parse("Mon to Sat 6 am to 10 am")["week"])["mon"] == "06:00-10:00"
    assert hours(parse("Mon 11 to 3")["week"]) == {"mon": "11:00-15:00"}
    assert hours(parse("Mon 12 to 2")["week"]) == {"mon": "12:00-14:00"}
    assert hours(parse("Mon 6 pm to 12")["week"]) == {"mon": "18:00-24:00"}


def test_overlapping_ranges_are_joined_and_said():
    got = parse("Mon 9 to 1 and 12 to 5")
    assert hours(got["week"]) == {"mon": "09:00-17:00"} and any("overlapped" in n for n in got["notes"])


def test_what_it_cannot_read_is_listed_and_not_confident():
    got = parse("Mon to Sat 10 to 1 except during Diwali")
    assert got["confident"] is False and any("Diwali".lower() in n.lower() for n in got["notes"])
    assert hours(got["week"])["mon"] == "10:00-13:00"
    nothing = parse("we are always happy to help")
    assert nothing["confident"] is False and nothing["source"] is None and not any(nothing["week"].values())
    assert nothing["notes"]
    only_days = parse("Mon to Sat")
    assert only_days["confident"] is False and any("No hours" in n for n in only_days["notes"])
    midnight = parse("Mon 10 pm to 2 am")
    assert midnight["confident"] is False and not midnight["week"]["mon"]


def test_the_answer_is_always_a_valid_week():
    from services import schedules
    for text in ("Mon to Sat 10 to 1 and 5 to 9, Sunday closed", "24 hours", "Mon 9 to 1 and 12 to 5", "Fri to Mon 10 to 2",
                 "garbage", "", "99 to 100", "Mon 13:00 to 25:00"):
        got = parse(text)
        assert schedules.normalise_week(got["week"]) == got["week"]


# ---------------------------------------------------------------------------------------------- the model fallback

def fake_model(monkeypatch, reply, calls=None):
    def chat(messages, **kw):
        if calls is not None:
            calls.append(messages)
        if isinstance(reply, Exception):
            raise reply
        return llm.ChatResult(reply, "fake", "fake-model", 1, {})
    monkeypatch.setattr(llm, "chat", chat)


def test_the_model_is_asked_only_when_the_parser_understood_nothing(monkeypatch):
    calls = []
    fake_model(monkeypatch, '{"week": {"mon": [{"start": "10:00", "end": "14:00"}]}, "slot_minutes": 20}', calls)
    assert hp.parse("Mon to Sat 10 to 1", TODAY)["source"] == "parser" and calls == []
    got = hp.parse("we open at ten in the morning on mondays and close at two", TODAY)
    assert len(calls) == 1 and got["source"] == "model" and got["confident"] is False
    assert hours(got["week"]) == {"mon": "10:00-14:00"} and got["slot_minutes"] == 20
    assert got["notes"] == ["Read by the language model, please check the hours."]


@pytest.mark.parametrize("reply", [
    "not json at all", '{"week": {"mon": [{"start": "14:00", "end": "10:00"}]}}',
    '{"week": {"mon": [{"start": "10:00", "end": "12:00"}, {"start": "11:00", "end": "13:00"}]}}',
    '{"week": {"holiday": []}}', '{"week": {"mon": []}}', "[]", llm.LLMUnavailable({"fake": "no model"}), RuntimeError("boom"),
])
def test_a_bad_model_answer_is_ignored(monkeypatch, reply):
    fake_model(monkeypatch, reply)
    got = hp.parse("somewhat unreadable text", TODAY)
    assert got["source"] is None and got["confident"] is False and not any(got["week"].values())


def test_the_model_answer_is_cleaned(monkeypatch):
    fake_model(monkeypatch, '{"week": {"mon": [{"start": "9:00", "end": "23:59"}]}, "slot_minutes": 3, '
                            '"closed_dates": ["2026-12-25", "xmas"]}')
    got = hp.parse("opens nine, shuts at midnight on monday", TODAY)
    assert got["week"]["mon"] == [{"start": "09:00", "end": "24:00"}]
    assert got["slot_minutes"] is None and got["closed_dates"] == ["2026-12-25"]


# ---------------------------------------------------------------------------------------------- the route

def test_parse_hours_route(app_client):
    r = app_client.post("/api/schedules/parse-hours", headers=LOCAL,
                        json={"text": "Mon to Sat 10 to 1 and 5 to 9, Sunday closed"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == {"week", "slot_minutes", "closed_dates", "notes", "confident", "source"}
    assert out["confident"] is True and out["slot_minutes"] is None and out["closed_dates"] == [] and out["notes"] == []
    assert hours(out["week"]) == same(DAYS[:6], "10:00-13:00,17:00-21:00") and out["week"]["sun"] == []
    hi = app_client.post("/api/schedules/parse-hours", headers=LOCAL, json={"text": "सोमवार से शनिवार सुबह 10 से 2"}).json()
    assert hours(hi["week"]) == same(DAYS[:6], "10:00-14:00") and hi["confident"] is True


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "   "}, {"text": "x" * 1001}, {}])
def test_parse_hours_input_is_checked(app_client, body):
    assert app_client.post("/api/schedules/parse-hours", headers=LOCAL, json=body).status_code == 422
