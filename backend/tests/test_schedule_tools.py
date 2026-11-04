"""The agent's scheduling tools (services/schedule_tools.py): check_availability, list_free_slots, book, cancel and
find_booking, called directly and through the spoken-turn endpoint with a scripted language model. The model never decides
what is free: the tools answer from the same engine as /api/schedules/{id}/check."""
import json
from datetime import date, datetime, timedelta

import pytest

from services import llm, schedule_tools as st, schedules as sch, tools
from tests.test_offline_agents import LOCAL, env, make_agent  # noqa: F401  (env is a fixture)
from tests.test_turn_stream import rig  # noqa: F401  (rig is a fixture)
from tests.test_turn_tools import Model, last_tool_json, tool_calls_result

NOW = datetime(2026, 11, 3, 9, 0)    # Tuesday
THURSDAY = "2026-11-05"
AGENT = "agent-1"


def clinic_week():
    return {d: [{"start": "10:00", "end": "13:00"}, {"start": "17:00", "end": "20:00"}]
            for d in ("mon", "tue", "wed", "thu", "fri", "sat")} | {"sun": []}


def open_week():
    return {d: [{"start": "00:00", "end": "24:00"}] for d in sch.DAYS}


@pytest.fixture()
def clock(monkeypatch):
    monkeypatch.setattr(sch, "now", lambda tz=None: NOW)


@pytest.fixture()
def clinic(clock):
    return sch.create({"name": "Doctor consultations", "slot_minutes": 30, "week": clinic_week(), "agent_id": AGENT})


def run(tool, **args):
    return st.run(tool, args, AGENT)


def book(**kw):
    args = {"date": THURSDAY, "time": "18:00", "name": "Priya Sharma", "phone": "9876543210", **kw}
    return run("book", **args)


# ---------------------------------------------------------------------------------------------- check_availability

def test_a_free_time_is_reported_free(clinic):
    ok, v = run("check_availability", date=THURSDAY, time="18:00")
    assert ok and v["available"] is True and v["schedule"] == "Doctor consultations" and v["weekday"] == "Thursday"
    assert "before" not in v and v["say"]["en"] == "Thursday at 6 pm is free."
    assert "Shall I book it" in v["next_step"]


def test_a_taken_time_comes_with_the_nearest_options(clinic):
    assert book()[0]
    ok, v = run("check_availability", date=THURSDAY, time="18:00")
    assert ok and v["available"] is False and v["reason"] == "full"
    assert v["before"] == {"date": THURSDAY, "weekday": "Thursday", "time": "17:30"}
    assert v["after"] == {"date": THURSDAY, "weekday": "Thursday", "time": "18:30"}
    assert v["alternatives"] == [v["before"], v["after"]]
    assert v["say"]["en"] == "Thursday at 6 pm is already taken. 5:30 pm or 6:30 pm, which would suit you?"
    assert v["say"]["hi"].startswith("गुरुवार शाम छह बजे वाला समय भरा हुआ है।")


def test_the_reasons_come_through(clinic):
    for args, reason in (({"date": "2026-11-08", "time": "11:00"}, "closed"), ({"date": THURSDAY, "time": "15:00"}, "outside_hours"),
                         ({"date": "2026-11-02", "time": "11:00"}, "past"), ({"date": "2026-12-25", "time": "11:00"}, "too_far"),
                         ({"date": THURSDAY}, "unclear")):
        ok, v = run("check_availability", **args)
        assert ok and v["available"] is False and v["reason"] == reason, (args, v)


def test_an_off_grid_time_is_not_a_start_with_the_neighbours(clinic):
    ok, v = run("check_availability", date=THURSDAY, time="10:10")
    assert ok and v["available"] is False and v["reason"] == "not_a_start"
    assert v["before"]["time"] < "10:10" < v["after"]["time"]
    assert v["alternatives"] == [v["before"], v["after"]]
    assert v["say"]["en"].startswith("We book every ")


def test_a_bad_date_is_an_error_the_model_can_repeat(clinic):
    for args in ({}, {"date": "Thursday"}, {"date": "2026-13-45"}):
        ok, v = run("check_availability", **args)
        assert not ok and isinstance(v, str) and "date" in v.lower()


def test_a_stay(clock):
    sch.create({"name": "Rooms", "kind": "nights", "capacity": 1, "check_in": "12:00", "check_out": "11:00",
                "week": open_week(), "agent_id": AGENT})
    ok, v = run("check_availability", date="2026-11-12", nights=2)
    assert ok and v["available"] and v["nights"] == 2
    ok, b = run("book", date="2026-11-12", nights=2, name="Rahul Verma", phone="9123456780")
    assert ok and b["nights"] == 2 and b["date"] == "2026-11-12" and b["check_out"] == "2026-11-14T11:00"
    ok, v = run("check_availability", date="2026-11-13", nights=2)
    assert v["available"] is False and v["reason"] == "full"
    assert v["before"] == {"date": "2026-11-10", "weekday": "Tuesday"} and v["after"] == {"date": "2026-11-14", "weekday": "Saturday"}
    ok, rooms = run("list_free_slots", date="2026-11-12")
    assert ok and rooms["free"][0] == {"date": "2026-11-14", "rooms_left": 1}


# ---------------------------------------------------------------------------------------------- list_free_slots

def test_free_slots_of_a_day(clinic):
    book(time="10:30")
    ok, v = run("list_free_slots", date=THURSDAY)
    assert ok and v["weekday"] == "Thursday" and v["count"] == 11 and v["slot_minutes"] == 30
    assert v["free"][:3] == ["10:00", "11:00", "11:30"] and len(v["free"]) == 11
    ok, none = run("list_free_slots", date="2026-11-08")
    assert ok and none["free"] == [] and none["reason"] == "closed" and none["next_free"]["date"] == "2026-11-09"


def test_a_long_list_is_shortened(clock):
    sch.create({"name": "All day", "slot_minutes": 15, "week": open_week(), "agent_id": AGENT})
    ok, v = run("list_free_slots", date=THURSDAY)
    assert v["count"] == 96 and len(v["free"]) == 16 and "80 later times" in v["more"]


# ---------------------------------------------------------------------------------------------- book and cancel

def test_booking_writes_the_row_for_this_front_desk(clinic):
    ok, v = book()
    assert ok and v == {"booking_id": "A10", "schedule": "Doctor consultations", "name": "Priya Sharma", "date": THURSDAY,
                        "weekday": "Thursday", "time": "18:00", "status": "booked", "say": v["say"]}
    assert "A10" in v["say"]["en"] and "phone" not in v
    row = next(b for b in st.sb._ap().all_bookings() if b["booking_id"] == "A10")
    assert row["agent_id"] == AGENT and row["schedule_id"] == "S1" and row["source"] == "agent" and row["phone"] == "9876543210"
    assert row["start"] == "2026-11-05T18:00" and row["end"] == "2026-11-05T18:30"


def test_a_taken_slot_is_refused_with_the_options(clinic):
    book()
    ok, v = book(name="Ravi Kumar")
    assert not ok and v["booked"] is False and v["reason"] == "full" and v["alternatives"][0]["time"] == "17:30"
    assert len(st.sb._ap().all_bookings()) == 1


@pytest.mark.parametrize("args,word", [({"name": "x"}, "name"), ({"phone": "123"}, "10 digits"), ({"phone": ""}, "phone"),
                                       ({"time": None}, "time"), ({"date": None}, "date")])
def test_bad_booking_input_says_what_is_wrong(clinic, args, word):
    ok, v = book(**args)
    assert not ok and word in v.lower() and st.sb._ap().all_bookings() == []


def test_callers_are_held_to_the_notice_and_the_horizon(clinic):
    sch.update("S1", {"min_notice_minutes": 240})
    ok, v = book(date="2026-11-03", time="10:00")
    assert not ok and v["reason"] == "too_soon"
    ok, v = book(date="2026-12-25", time="10:00")
    assert not ok and v["reason"] == "too_far"


def test_cancel_only_the_front_desks_own_future_bookings(clinic, clock):
    book()
    other = sch.create({"name": "Other desk", "slot_minutes": 30, "week": clinic_week(), "agent_id": "agent-2"})
    from services import schedule_bookings as sb
    sb.create(other, day=date(2026, 11, 5), at="10:00", name="Neha Joshi", phone="9000000001", source="staff")
    ok, v = run("cancel", booking_id="A11")
    assert not ok and "no booking A11" in v
    ok, v = run("cancel", booking_id=" a10 ")
    assert ok and v["status"] == "cancelled" and v["booking_id"] == "A10"
    ok, v = run("cancel", booking_id="A10")
    assert not ok and "already cancelled" in v
    assert run("check_availability", date=THURSDAY, time="18:00")[1]["available"] is True        # freed
    ok, v = run("cancel", booking_id="legacy")
    assert not ok


def test_a_doctor_booking_of_the_older_tool_cannot_be_cancelled_here(clinic, monkeypatch):
    from mcp_servers import appointments as ap
    monkeypatch.setattr(ap, "now", lambda: NOW)
    old = ap.book_appointment(name="Priya Sharma", phone="9876543210", date="2026-11-09", time="10:30")
    ok, v = run("cancel", booking_id=old["booking_id"])
    assert not ok and "no booking" in v


def test_find_booking_by_phone(clinic):
    book()
    book(time="10:00", date="2026-11-06", name="Priya Sharma")
    book(time="11:00", name="Ravi Kumar", phone="9000000002")
    ok, v = run("find_booking", phone="98765 43210")
    assert ok and [b["time"] for b in v["bookings"]] == ["18:00", "10:00"] or [b["date"] for b in v["bookings"]] == [THURSDAY, "2026-11-06"]
    assert all("phone" not in b for b in v["bookings"]) and len(v["bookings"]) == 2
    assert run("find_booking", phone="9999999999")[1] == {"bookings": []}
    assert not run("find_booking", phone="12")[0]


# ---------------------------------------------------------------------------------------------- which schedule

@pytest.fixture()
def two(clock):
    sch.create({"name": "Doctor consultations", "slot_minutes": 30, "week": clinic_week(), "agent_id": AGENT})
    sch.create({"name": "Lab tests", "slot_minutes": 15, "week": clinic_week(), "agent_id": AGENT})
    sch.create({"name": "Doctor consultations", "slot_minutes": 30, "week": clinic_week(), "agent_id": "agent-2"})


@pytest.mark.parametrize("said,expected", [("Doctor consultations", "S1"), ("doctor", "S1"), ("Doctor", "S1"),
                                           ("डॉक्टर", "S1"), ("consultation", "S1"), ("lab", "S2"), ("Lab Test", "S2"),
                                           ("lab tests please", "S2")])
def test_schedules_are_matched_by_name_loosely(two, said, expected):
    found, problem = st.resolve_schedule(AGENT, said)
    assert problem is None and found["id"] == expected


def test_with_several_schedules_the_caller_is_asked(two):
    for said in (None, "", "any", "something else"):
        found, problem = st.resolve_schedule(AGENT, said)
        assert found is None and "Doctor consultations" in problem and "Lab tests" in problem
    ok, v = run("check_availability", date=THURSDAY, time="11:00")
    assert not ok and "Lab tests" in v
    ok, v = run("check_availability", schedule="lab", date=THURSDAY, time="10:15")
    assert ok and v["schedule"] == "Lab tests" and v["available"]


def test_one_schedule_needs_no_name_and_none_means_none(clinic):
    assert st.resolve_schedule(AGENT, None)[0]["id"] == "S1"
    assert st.resolve_schedule(AGENT, "nonsense words")[0]["id"] == "S1"           # a single schedule is the only choice
    ok, v = st.run("check_availability", {"date": THURSDAY, "time": "11:00"}, "agent-without-schedules")
    assert not ok and "no schedules" in v


# ---------------------------------------------------------------------------------------------- through the turn endpoint

def next_day(weekday: int, min_days: int = 2) -> str:
    d = date.today() + timedelta(days=min_days)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d.isoformat()


def say(rig, agent_id, text, conv=None):
    r = rig.client.post(f"/api/conversations/{agent_id}/turn", headers=LOCAL,
                        data={"text": text, **({"conversation_id": conv} if conv else {})})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def desk(rig, monkeypatch):
    """A front desk with one schedule on the real clock; tools in the native protocol the scripted model speaks."""
    monkeypatch.setenv("VAKYA_TOOL_PROTOCOL", "native")
    day = next_day(3)
    agent = make_agent(rig.client, tools=["schedules"], disclose_ai=False)
    schedule = sch.create({"name": "Doctor consultations", "slot_minutes": 30, "week": clinic_week(), "agent_id": agent["id"]})
    return rig, agent, schedule, day


def bookings():
    return [b for b in st.sb._ap().all_bookings()]


def after_last_user(messages):
    idx = max(i for i, m in enumerate(messages) if m["role"] == "user")
    return messages[idx]["content"].lower(), [m for m in messages[idx + 1:] if m["role"] == "tool"]


def test_the_agent_is_given_the_scheduling_tools_and_the_rule(desk, monkeypatch):
    rig, agent, schedule, day = desk
    model = Model(monkeypatch, lambda m, kw: llm.ChatResult("Hello.", "fake", "fake-model", 1, {}))
    say(rig, agent["id"], "hi")
    first = model.requests[0]
    assert [t["function"]["name"] for t in first["tools"]] == \
        ["check_availability", "list_free_slots", "book", "cancel", "find_booking", "forget_me"]
    system = first["messages"][0]["content"]
    assert "never say a time is free" in system and "`before` and `after`" in system and "Shall I book it?" in system


def test_an_agent_with_schedules_gets_the_tools_without_the_setting(rig, monkeypatch):
    monkeypatch.setenv("VAKYA_TOOL_PROTOCOL", "native")
    agent = make_agent(rig.client, disclose_ai=False)
    model = Model(monkeypatch, lambda m, kw: llm.ChatResult("Hello.", "fake", "fake-model", 1, {}))
    say(rig, agent["id"], "hi")
    assert "tools" not in model.requests[0]            # no schedules, no tools
    sch.create({"name": "Visits", "slot_minutes": 30, "week": clinic_week(), "agent_id": agent["id"]})
    say(rig, agent["id"], "hi again")
    assert [t["function"]["name"] for t in model.requests[1]["tools"]][:2] == ["check_availability", "list_free_slots"]


def test_the_doctor_list_tool_gives_way_to_the_schedules(rig, monkeypatch, tmp_path):
    monkeypatch.setenv("VAKYA_TOOL_PROTOCOL", "native")
    monkeypatch.setenv("VAKYA_APPOINTMENTS_FILE", str(tmp_path / "a.json"))
    h = tools.ToolHub()
    monkeypatch.setattr(tools, "hub", h)
    agent = make_agent(rig.client, tools=["appointments"], disclose_ai=False)
    sch.create({"name": "Visits", "slot_minutes": 30, "week": clinic_week(), "agent_id": agent["id"]})
    model = Model(monkeypatch, lambda m, kw: llm.ChatResult("Hello.", "fake", "fake-model", 1, {}))
    try:
        say(rig, agent["id"], "hi")
    finally:
        h.close()
    names = [t["function"]["name"] for t in model.requests[0]["tools"]]
    assert names == ["check_availability", "list_free_slots", "book", "cancel", "find_booking", "forget_me"]


def test_checking_a_taken_time_offers_before_and_after(desk, monkeypatch):
    rig, agent, schedule, day = desk
    from services import schedule_bookings as sb
    sb.create(schedule, day=date.fromisoformat(day), at="18:00", name="Asha Rao", phone="9000000003", source="staff")

    def plan(messages, kw):
        text, results = after_last_user(messages)
        if not results:
            return tool_calls_result(("check_availability", {"date": day, "time": "6 pm"}))   # words are resolved in code
        return llm.ChatResult(last_tool_json(messages)["say"]["en"], "fake", "fake-model", 1, {})

    Model(monkeypatch, plan)
    out = say(rig, agent["id"], "Is Thursday 6 pm free?")
    [call] = out["tool_calls"]
    assert call["name"] == "check_availability" and call["ok"] is True and call["arguments"]["time"] == "18:00"
    assert call["result"] == f"18:00 on {day} is not free, 2 other option(s)"
    assert "6 pm is already taken. 5:30 pm or 6:30 pm, which would suit you?" in out["agent_text"]
    assert len(bookings()) == 1


def test_booking_after_a_choice_reads_back_and_waits_for_a_yes(desk, monkeypatch):
    rig, agent, schedule, day = desk
    monkeypatch.setenv("TOOLS_READBACK", "on")
    from services import schedule_bookings as sb
    sb.create(schedule, day=date.fromisoformat(day), at="18:00", name="Asha Rao", phone="9000000003", source="staff")
    d = date.fromisoformat(day)
    spoken = f"{d.strftime('%A')} {d.day} {d.strftime('%B')} at 5:30 pm"
    args = {"date": day, "time": "17:30", "name": "Priya Sharma", "phone": "9876543210"}

    def plan(messages, kw):
        text, results = after_last_user(messages)
        if "free?" in text:
            if not results:
                return tool_calls_result(("check_availability", {"date": day, "time": "18:00"}))
            last_tool_json(messages)       # the model read the options...
            return llm.ChatResult("Thursday is busy at six, but half past five or half past six are free. Which suits you?",
                                  "fake", "fake-model", 1, {})
        if not results:
            return tool_calls_result(("book", args))
        body = results[-1]["content"]
        if "NOT DONE" in body:
            return llm.ChatResult(f"{spoken}, for Priya Sharma, number ending 3210. Shall I book it?", "fake", "fake-model", 1, {})
        return llm.ChatResult(f"Booked {last_tool_json(messages)['booking_id']}.", "fake", "fake-model", 1, {})

    Model(monkeypatch, plan)
    first = say(rig, agent["id"], "Is Thursday 6 pm free?")
    conv = first["conversation_id"]
    second = say(rig, agent["id"], "5:30 then, I am Priya Sharma, 9876543210", conv)
    assert second["tool_calls"][0]["name"] == "book" and second["tool_calls"][0]["ok"] is False
    assert second["agent_text"].endswith("Shall I book it?") and len(bookings()) == 1       # nothing booked yet
    third = say(rig, agent["id"], "yes please", conv)
    assert third["tool_calls"][0]["ok"] is True and third["agent_text"] == "Booked A11."
    mine = [b for b in bookings() if b["booking_id"] == "A11"][0]
    assert mine["start"] == f"{day}T17:30" and mine["agent_id"] == agent["id"]
    assert mine["conversation_id"] == conv and mine["receipt_id"]                      # linked to the call and its receipt
    row = next(b for b in rig.client.get("/api/bookings?schedule_id=" + schedule["id"], headers=LOCAL).json()["bookings"]
               if b["id"] == "A11")
    assert row["conversation_id"] == conv and row["schedule_name"] == "Doctor consultations"


def test_cancelling_needs_the_callers_yes_first(desk, monkeypatch):
    rig, agent, schedule, day = desk
    from services import schedule_bookings as sb
    sb.create(schedule, day=date.fromisoformat(day), at="11:00", name="Priya Sharma", phone="9876543210", source="staff")
    monkeypatch.setenv("TOOLS_READBACK", "on")

    def plan(messages, kw):
        text, results = after_last_user(messages)
        if not results:
            if "cancel" in text and "yes" not in text:
                return tool_calls_result(("cancel", {"booking_id": "A10"}))       # too eager: without asking first
            if "yes" in text:
                return tool_calls_result(("cancel", {"booking_id": "A10"}))
            return llm.ChatResult("hello", "fake", "fake-model", 1, {})
        body = results[-1]["content"]
        if "NOT DONE" in body:
            return llm.ChatResult("Shall I cancel booking A10?", "fake", "fake-model", 1, {})
        return llm.ChatResult("Cancelled booking A10.", "fake", "fake-model", 1, {})

    Model(monkeypatch, plan)
    first = say(rig, agent["id"], "please cancel my booking A10")
    assert first["tool_calls"][0]["ok"] is False and bookings()[0]["status"] == "booked"
    assert first["agent_text"] == "Shall I cancel booking A10?"
    second = say(rig, agent["id"], "yes", first["conversation_id"])
    assert second["tool_calls"][0]["ok"] is True and bookings()[0]["status"] == "cancelled"


def test_a_booking_claimed_without_the_tool_is_replaced(desk, monkeypatch):
    rig, agent, schedule, day = desk
    Model(monkeypatch, lambda m, kw: llm.ChatResult(f"Your appointment is booked for {day} at 6 pm.", "fake", "fake-model", 1, {}))
    out = say(rig, agent["id"], "book me for Thursday 6 pm")
    assert out["agent_text"] == tools.UNCONFIRMED_LINE and bookings() == []
