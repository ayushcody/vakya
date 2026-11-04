"""Running late and the token queue (services/delays.py, routers/delays.py, the queue_status tool): tokens renumber on a
cancellation, the delay clears itself at midnight, the expected time follows the formula, the spoken lines in three
languages, the walk-in, and the call that tells a patient the new time (with the telephony fakes of test_phone.py). The clock
is Tuesday 3 November 2026, 17:30; every file is under a temp dir."""
from datetime import datetime

import pytest

from services import delays, schedule_bookings as sb, schedule_tools as st, schedules as sch
from tests.test_phone import configure, rig, vobiz  # noqa: F401  (fixtures)
from tests.test_offline_agents import LOCAL, env, make_agent  # noqa: F401  (env is a fixture)

NOW = datetime(2026, 11, 3, 17, 30)
AGENT = "agent-1"
DAY = "2026-11-03"


@pytest.fixture()
def clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(sch, "now", lambda tz=None: state["now"])
    return state


def week():
    return {d: [{"start": "10:00", "end": "13:00"}, {"start": "17:00", "end": "20:00"}]
            for d in ("mon", "tue", "wed", "thu", "fri", "sat")} | {"sun": []}


@pytest.fixture()
def clinic(clock):
    return sch.create({"name": "Doctor consultations", "slot_minutes": 30, "week": week(), "agent_id": AGENT})


def staff(schedule, at, name, phone="9876500011", day=DAY):
    """A staff booking made that morning (the clock of the test is later, so the patient's slot can already be under way)."""
    return sb.create(schedule, day=datetime.fromisoformat(day).date(), at=at, name=name, phone=phone,
                     now=datetime(2026, 11, 3, 9, 0))


def test_a_token_is_the_place_among_the_days_live_bookings_and_renumbers_on_a_cancellation(clinic):
    b3 = staff(clinic, "19:00", "Chitra Joshi", "9876500013")
    b1 = staff(clinic, "18:00", "Asha Rao", "9876500011")
    b2 = staff(clinic, "18:30", "Bharat Patil", "9876500012")
    tokens = sb.token_map()
    assert (tokens[b1["booking_id"]], tokens[b2["booking_id"]], tokens[b3["booking_id"]]) == (1, 2, 3)
    assert sb.public(b2, tokens)["token"] == 2 and "token" not in sb.public(b2)
    sb.cancel(b1["booking_id"])
    tokens = sb.token_map()
    assert (tokens[b2["booking_id"]], tokens[b3["booking_id"]]) == (1, 2) and b1["booking_id"] not in tokens


def test_tomorrow_has_its_own_numbering_and_stays_and_yesterday_have_none(clinic, clock):
    t1 = staff(clinic, "18:00", "Asha Rao")
    n1 = staff(clinic, "18:00", "Bharat Patil", day="2026-11-04")
    tokens = sb.token_map()
    assert tokens[t1["booking_id"]] == 1 and tokens[n1["booking_id"]] == 1
    clock["now"] = datetime(2026, 11, 4, 9, 0)
    assert t1["booking_id"] not in sb.token_map()


def test_booked_by_and_relation_are_passed_on_when_the_row_has_them(clinic):
    row = staff(clinic, "18:00", "Sunita Kulkarni")
    row.update({"booked_by": "Rahul", "relation": "son"})
    assert sb.public(row)["booked_by"] == "Rahul" and sb.public(row)["relation"] == "son"
    assert "booked_by" not in sb.public(staff(clinic, "18:30", "Bharat Patil"))


def test_the_delay_is_set_cleared_validated_and_ends_with_the_day(clinic, clock):
    assert delays.state_of(clinic) is None
    d = delays.set_delay(clinic, 40, "  emergency   surgery ")
    assert d["minutes_late"] == 40 and d["reason"] == "emergency surgery" and d["now_serving_token"] is None
    for bad in (-1, 481, "40", True, 4.5):
        with pytest.raises(sch.ScheduleError):
            delays.set_delay(clinic, bad)
    with pytest.raises(sch.ScheduleError):
        delays.set_delay(clinic, 10, "x" * 81)
    clock["now"] = datetime(2026, 11, 4, 9, 0)
    assert delays.state_of(clinic) is None                       # tomorrow it is gone, without anyone clearing it
    delays.set_delay(clinic, 15)                                  # and the next write drops the old entry from the file
    assert list(delays._read(delays.data_path())["delays"].values())[0]["date"] == "2026-11-04"
    assert delays.set_delay(clinic, 0) is None


def test_a_stay_has_no_delay(clock):
    hotel = sch.create({"name": "Rooms", "kind": "nights", "capacity": 2, "check_in": "12:00", "check_out": "11:00",
                        "week": {d: [{"start": "00:00", "end": "24:00"}] for d in sch.DAYS}, "agent_id": AGENT})
    with pytest.raises(sch.ScheduleError):
        delays.set_delay(hotel, 20)


def test_expected_time_is_the_slot_plus_the_delay_without_a_token_being_served(clinic):
    for at, name in (("17:00", "Asha Rao"), ("18:00", "Bharat Patil"), ("18:30", "Chitra Joshi")):
        staff(clinic, at, name)
    delays.set_delay(clinic, 40, "emergency")
    q = delays.queue(clinic)
    assert [(t["token"], t["slot_start"], t["expected_at"], t["status"]) for t in q["tokens"]] == [
        (1, "17:00", "17:40", "waiting"), (2, "18:00", "18:40", "waiting"), (3, "18:30", "19:10", "waiting")]
    assert [t["people_ahead"] for t in q["tokens"]] == [0, 1, 2]
    assert [a["booking_id"] for a in q["affected"]][0] == q["tokens"][0]["booking_id"]
    assert len(q["affected"]) == 3
    assert q["affected"][1]["suggested_time"] == "18:40" and q["late_say"]["en"].startswith("The doctor is running about 40")
    assert q["tokens"][1]["expected_say"] == {"en": "about 6:40 pm", "hi": "लगभग शाम 6:40", "mr": "सुमारे संध्याकाळी 6:40"}


def test_a_small_slip_is_not_listed_for_a_call(clinic):
    staff(clinic, "18:00", "Asha Rao")
    delays.set_delay(clinic, 10)
    q = delays.queue(clinic)
    assert q["tokens"][0]["expected_at"] == "18:10" and q["affected"] == []


def test_with_a_token_being_served_the_queue_ahead_can_push_the_time_later_than_the_delay(clinic):
    for at, name in (("17:00", "Asha Rao"), ("17:30", "Bharat Patil"), ("18:00", "Chitra Joshi"), ("18:30", "Dev Naik")):
        staff(clinic, at, name)
    delays.set_delay(clinic, 10)
    delays.set_now_serving(clinic, 1, delays.queue(clinic)["count"])
    q = delays.queue(clinic)
    # now 17:30, 30-minute slots: token 4 has 3 ahead, so max(18:30 + 10 min, 17:30 + 90 min) = 19:00; token 3: max(18:10, 18:30)
    assert [(t["status"], t["people_ahead"], t["expected_at"]) for t in q["tokens"]] == [
        ("now", 0, "17:10"), ("waiting", 1, "18:00"), ("waiting", 2, "18:30"), ("waiting", 3, "19:00")]
    delays.set_now_serving(clinic, "next", 4)
    q = delays.queue(clinic)
    assert [t["status"] for t in q["tokens"]] == ["seen", "now", "waiting", "waiting"] and q["now_serving"] == 2


def test_the_serving_token_is_checked(clinic):
    staff(clinic, "18:00", "Asha Rao")
    with pytest.raises(sch.ScheduleError):
        delays.set_now_serving(clinic, 5, 1)
    assert delays.set_now_serving(clinic, "next", 1)["now_serving_token"] == 1
    with pytest.raises(sch.ScheduleError) as e:
        delays.set_now_serving(clinic, "next", 1)
    assert e.value.code == "NO_MORE_TOKENS"
    assert delays.set_now_serving(clinic, 0, 1) is None          # nobody with the doctor and no delay: nothing is kept


# ---------------------------------------------------------------------------------------------- the tool

def tool(**args):
    return st.run("queue_status", args, AGENT)


def test_queue_status_answers_with_the_token_the_people_ahead_and_the_time(clinic):
    for at, name, phone in (("17:30", "Asha Rao", "9876500011"), ("18:00", "Bharat Patil", "9876500012"),
                            ("18:30", "Chitra Joshi", "9876500013")):
        staff(clinic, at, name, phone)
    delays.set_delay(clinic, 40, "emergency")
    ok, v = tool(phone="98765 00013")
    assert ok and v["found"] and v["token"] == 3 and v["people_ahead"] == 2 and v["expected_at"] == "19:10"
    assert v["minutes_late"] == 40 and v["reason"] == "emergency" and v["slot_time"] == "18:30" and v["name"] == "Chitra Joshi"
    assert v["say"]["en"] == ("You are token 3; 2 patients are ahead of you. The doctor is running about 40 minutes late "
                              "today, so please come by about 7:10 pm.")
    assert v["say"]["hi"] == ("आपका टोकन 3 है; आपसे पहले 2 मरीज़ हैं। आज डॉक्टर लगभग 40 मिनट देरी से चल रहे हैं, इसलिए कृपया "
                              "लगभग शाम 7:10 तक आइए।")
    assert v["say"]["mr"] == ("तुमचा टोकन 3 आहे; तुमच्या आधी 2 रुग्ण आहेत. आज डॉक्टर सुमारे 40 मिनिटे उशिरा चालले आहेत, म्हणून कृपया "
                              "सुमारे संध्याकाळी 7:10 पर्यंत या.")
    assert "9876500013" not in str(v)
    ok, by_name = tool(name="bharat")
    assert ok and by_name["token"] == 2 and by_name["people_ahead"] == 1


def test_queue_status_without_a_delay_gives_the_booked_time(clinic):
    staff(clinic, "18:00", "Asha Rao")
    ok, v = tool(name="Asha Rao")
    assert ok and v["say"]["en"] == "You are token 1; nobody is ahead of you. Your time is 6 pm." and v["minutes_late"] == 0


def test_queue_status_with_an_unknown_caller_an_ambiguous_name_or_nothing_to_go_on(clinic):
    staff(clinic, "18:00", "Asha Rao", "9876500011")
    staff(clinic, "18:30", "Asha Kulkarni", "9876500012")
    delays.set_delay(clinic, 20)
    ok, v = tool(name="Zoya")
    assert ok and v["found"] is False and v["minutes_late"] == 20 and "cannot find" in v["say"]["en"]
    ok, msg = tool(name="Asha")
    assert not ok and "phone number" in msg
    ok, v = tool(phone="9876500012")
    assert ok and v["token"] == 2
    assert tool()[0] is False and tool(phone="123")[0] is False


def test_book_for_today_mentions_the_delay_and_a_later_day_does_not(clinic):
    delays.set_delay(clinic, 30, "doctor stuck in surgery")
    ok, v = st.run("book", {"date": DAY, "time": "19:00", "name": "Priya Sharma", "phone": "9876543210"}, AGENT)
    assert ok and v["running_late"]["minutes_late"] == 30 and v["token"] == 1
    assert v["running_late"]["say"]["hi"] == "आज डॉक्टर लगभग 30 मिनट देरी से चल रहे हैं।"
    ok, v = st.run("book", {"date": "2026-11-04", "time": "18:00", "name": "Priya Sharma", "phone": "9876543210"}, AGENT)
    assert ok and "running_late" not in v and v["token"] == 1


def test_the_prompt_block_only_exists_while_a_delay_is_set(clinic):
    assert delays.prompt_block(AGENT) == "" and delays.prompt_block(None) == ""
    delays.set_delay(clinic, 40, "emergency")
    block = delays.prompt_block(AGENT)
    assert "the doctor is running 40 minutes late: emergency" in block and "queue_status" in block
    assert delays.prompt_block("another-agent") == ""


# ---------------------------------------------------------------------------------------------- the API

def api(client, path, method="get", **body):
    r = getattr(client, method)(f"/api/schedules/{path}", headers=LOCAL, **({"json": body} if body else {}))
    return r


def test_the_api_sets_the_delay_moves_the_token_and_lists_the_queue(app_client, clinic):
    sid = clinic["id"]
    staff(clinic, "18:00", "Asha Rao")
    staff(clinic, "18:30", "Bharat Patil")
    r = api(app_client, f"{sid}/delay", "post", minutes_late=45, reason="surgery")
    assert r.status_code == 200 and r.json()["minutes_late"] == 45 and len(r.json()["affected"]) == 2
    assert r.json()["calling"]["ready"] is False and "VOBIZ_AUTH_ID" in r.json()["calling"]["missing"]
    r = api(app_client, f"{sid}/now-serving", "post", token="next")
    assert r.status_code == 200 and r.json()["now_serving"] == 1 and r.json()["tokens"][0]["status"] == "now"
    assert api(app_client, f"{sid}/now-serving", "post", token=9).json()["code"] == "INVALID_TOKEN"
    assert api(app_client, f"{sid}/now-serving", "post", token="soon").status_code == 422
    assert api(app_client, f"{sid}/delay", "post", minutes_late=9999).json()["code"] == "INVALID_MINUTES"
    today = api(app_client, f"{sid}/today").json()
    assert today["count"] == 2 and today["minutes_late"] == 45 and today["now_serving"] == 1
    assert api(app_client, f"{sid}/delay", "post", minutes_late=0).json()["minutes_late"] == 0
    assert api(app_client, "S404/today").status_code == 404


def test_the_api_refuses_a_stay_and_adds_a_walk_in_with_a_token(app_client, clinic):
    hotel = sch.create({"name": "Rooms", "kind": "nights", "capacity": 2, "check_in": "12:00", "check_out": "11:00",
                        "week": {d: [{"start": "00:00", "end": "24:00"}] for d in sch.DAYS}, "agent_id": AGENT})
    assert api(app_client, f"{hotel['id']}/today").json()["code"] == "NOT_TIME_SLOTS"
    staff(clinic, "18:00", "Asha Rao")
    r = api(app_client, f"{clinic['id']}/walk-in", "post", name="Ganesh Pawar", phone="9876500014")
    assert r.status_code == 200, r.text
    b = r.json()["booking"]
    assert b["token"] == 2 and b["time"] == "18:30"                  # 18:00 is taken: the first free slot after now
    rows = {t["booking_id"]: t for t in r.json()["today"]["tokens"]}
    assert rows[b["booking_id"]]["source"] == "walk_in"
    assert api(app_client, f"{clinic['id']}/walk-in", "post", name="1").status_code == 422


def test_a_walk_in_after_the_last_slot_is_a_409(app_client, clinic, clock):
    clock["now"] = datetime(2026, 11, 3, 19, 45)
    r = api(app_client, f"{clinic['id']}/walk-in", "post", name="Ganesh Pawar")
    assert r.status_code == 409 and r.json()["code"] == "NO_SLOT_LEFT"


# ---------------------------------------------------------------------------------------------- "call to let them know"

def test_telling_a_patient_needs_the_phone_set_up_and_says_so_in_plain_words(app_client, clinic):
    b = staff(clinic, "18:00", "Asha Rao")
    delays.set_delay(clinic, 40)
    r = app_client.post(f"/api/schedules/{clinic['id']}/notify/{b['booking_id']}", headers=LOCAL)
    assert r.status_code == 409 and r.json()["code"] == "CALLING_NOT_SET_UP"
    assert "VOBIZ_AUTH_ID" in r.json()["error"] and "tell the patient yourself" in r.json()["error"]


def test_nothing_to_tell_when_the_time_has_not_moved(app_client, clinic):
    b = staff(clinic, "18:00", "Asha Rao")
    delays.set_delay(clinic, 5)
    assert app_client.post(f"/api/schedules/{clinic['id']}/notify/{b['booking_id']}", headers=LOCAL).json()["code"] == "NOTHING_TO_TELL"
    assert app_client.post(f"/api/schedules/{clinic['id']}/notify/A999", headers=LOCAL).status_code == 404


def test_the_call_rings_the_patient_and_the_notice_is_matched_to_that_call(rig, vobiz, clock, monkeypatch):  # noqa: F811
    configure(monkeypatch, PHONE_ALLOWED_NUMBERS="+919876500011")
    agent = make_agent(rig.client)
    desk = agent["name"]
    clinic = sch.create({"name": "Doctor consultations", "slot_minutes": 30, "week": week(), "agent_id": agent["id"]})
    b = staff(clinic, "18:00", "Asha Rao", "9876500011")
    delays.set_delay(clinic, 40, "emergency")
    r = rig.client.post(f"/api/schedules/{clinic['id']}/notify/{b['booking_id']}", headers=LOCAL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "calling" and body["call_sid"] == "uuid-abc123" and "9876500011" not in str(body["to"])
    assert vobiz.sent[0]["json"]["to"] == "+919876500011"
    assert body["say"]["en"] == (f"Namaste, this is {desk}. The doctor is running about 40 minutes late today. "
                                 "Your 6 pm visit is now around 6:40 pm. Is that okay, or would you like another time?")
    assert body["say"]["hi"].startswith(f"नमस्ते, यह {desk} से कॉल है।") and "शाम 6:40" in body["say"]["hi"]
    assert "संध्याकाळी 6:40" in body["say"]["mr"]

    class Session:
        party = "+919876500011"

    from services import call_control
    call_control.register("conv-1", Session())
    call_control.register("conv-2", type("Other", (), {"party": "+919000000000"})())
    try:
        note = delays.prompt_block(agent["id"], "conv-1")
        assert f"Namaste, this is {desk}" in note and f"cancel booking {b['booking_id']}" in note
        assert "Namaste" not in delays.prompt_block(agent["id"], "conv-2")
    finally:
        call_control.unregister("conv-1")
        call_control.unregister("conv-2")


def test_a_refused_call_forgets_the_notice(rig, vobiz, clock, monkeypatch):  # noqa: F811
    configure(monkeypatch, PHONE_ALLOWED_NUMBERS="+919876500011")
    agent = make_agent(rig.client)
    clinic = sch.create({"name": "Doctor consultations", "slot_minutes": 30, "week": week(), "agent_id": agent["id"]})
    b = staff(clinic, "18:00", "Asha Rao", "9876500011")
    delays.set_delay(clinic, 40)
    vobiz.status, vobiz.body = 402, {"error": "Account balance is too low to place the call"}
    r = rig.client.post(f"/api/schedules/{clinic['id']}/notify/{b['booking_id']}", headers=LOCAL)
    assert r.status_code == 503 and r.json()["code"] == "VOBIZ_OUT_OF_CREDIT"
    assert delays._notices == {}


def test_book_accepts_a_relation_argument_and_still_books(clinic):
    ok, v = st.run("book", {"date": "2026-11-04", "time": "18:00", "name": "Sunita Kulkarni", "phone": "9876543210",
                            "relation": "mother"}, AGENT)
    assert ok and v["status"] == "booked" and v["token"] == 1
    book_tool = next(t for t in st.TOOLS if t["tool"] == "book")
    assert "relation" in book_tool["schema"]["properties"] and "relation" not in book_tool["schema"]["required"]



def test_one_patient_ahead_is_singular_in_every_language():
    from services import delays as d
    t = {"token": 2, "people_ahead": 1, "status": "waiting", "slot_start": "18:00", "expected_at": "18:00"}
    q = {"minutes_late": 0, "reason": None, "date": "2026-11-05"}
    s = {"name": "Doctor consultations", "timezone": "Asia/Kolkata"}
    lines = {lg: d._status_line(s, q, t, lg) for lg in ("en", "hi", "mr")}
    assert "1 patient is ahead of you" in lines["en"]
    assert "आपसे पहले 1 मरीज़ है" in lines["hi"] and "मरीज़ हैं" not in lines["hi"]
    assert "तुमच्या आधी 1 रुग्ण आहे" in lines["mr"] and "रुग्ण आहेत" not in lines["mr"]
