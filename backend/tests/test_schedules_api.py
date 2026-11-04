"""/api/schedules (the model, the calendar, blocks, the availability answer) and the staff bookings of /api/bookings, with
temporary files and the clock set to Tuesday 3 November 2026, 09:00 (Asia/Kolkata)."""
from datetime import datetime

import pytest

from services import schedules as sch

LOCAL = {"storage-mode": "local"}
NOW = datetime(2026, 11, 3, 9, 0)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(sch, "now", lambda tz=None: NOW)
    monkeypatch.delenv("VAKYA_TIMEZONE", raising=False)


def clinic_week():
    return {d: [{"start": "10:00", "end": "13:00"}, {"start": "17:00", "end": "20:00"}]
            for d in ("mon", "tue", "wed", "thu", "fri", "sat")} | {"sun": []}


def new(client, **kw):
    body = {"name": "Doctor consultations", "slot_minutes": 30, "capacity": 1, "week": clinic_week(),
            "agent_id": "agent-1", **kw}
    r = client.post("/api/schedules", headers=LOCAL, json=body)
    assert r.status_code == 200, r.text
    return r.json()["schedule"]


def hotel(client):
    r = client.post("/api/schedules", headers=LOCAL, json={
        "name": "Rooms", "kind": "nights", "capacity": 2, "check_in": "12:00", "check_out": "11:00", "agent_id": "agent-1",
        "week": {d: [{"start": "00:00", "end": "24:00"}] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}})
    assert r.status_code == 200, r.text
    return r.json()["schedule"]


def check(client, sid, **body):
    r = client.post(f"/api/schedules/{sid}/check", headers=LOCAL, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def staff_book(client, sid, start, name="Asha Rao", **kw):
    return client.post("/api/bookings", headers=LOCAL, json={"schedule_id": sid, "start": start, "name": name, **kw})


# ---------------------------------------------------------------------------------------------- CRUD

def test_create_list_get_update(app_client):
    s = new(app_client, description="Walk-ins after 5")
    assert s["id"] == "S1" and s["agent_id"] == "agent-1" and s["kind"] == "slots" and s["accent"] == "violet"
    assert s["timezone"] == "Asia/Kolkata" and s["min_notice_minutes"] == 60 and s["horizon_days"] == 30
    assert s["summary"] == {"today_free": 12, "today_booked": 0, "next_free": "2026-11-03T10:00:00+05:30",
                            "hours_text": "Mon to Sat, 10:00 to 13:00 and 17:00 to 20:00"}
    new(app_client, name="Other desk", agent_id="agent-2")
    rows = app_client.get("/api/schedules?agent_id=agent-1", headers=LOCAL).json()["schedules"]
    assert [r["id"] for r in rows] == ["S1"] and rows[0]["summary"]["today_free"] == 12
    assert len(app_client.get("/api/schedules", headers=LOCAL).json()["schedules"]) == 2
    assert app_client.get("/api/schedules/S1", headers=LOCAL).json()["schedule"]["name"] == "Doctor consultations"
    r = app_client.put("/api/schedules/S1", headers=LOCAL, json={"name": "Visits", "slot_minutes": 20, "accent": "teal"})
    got = r.json()["schedule"]
    assert r.status_code == 200 and got["name"] == "Visits" and got["slot_minutes"] == 20 and got["accent"] == "teal"
    assert got["week"] == s["week"] and got["created_at"] == s["created_at"]


def test_a_week_with_one_range_replaced_and_a_full_object_put(app_client):
    s = new(app_client)
    again = {k: v for k, v in s.items() if k != "summary"} | {"capacity": 3}
    assert app_client.put("/api/schedules/S1", headers=LOCAL, json=again).json()["schedule"]["capacity"] == 3


@pytest.mark.parametrize("body,code", [
    ({"name": ""}, "INVALID_SCHEDULE"),
    ({"name": "A", "slot_minutes": 2}, "INVALID_SCHEDULE"),
    ({"name": "A", "week": {"mon": [{"start": "10:00", "end": "09:00"}]}}, "INVALID_RANGE"),
    ({"name": "A", "week": {"mon": [{"start": "09:00", "end": "11:00"}, {"start": "10:00", "end": "12:00"}]}},
     "OVERLAPPING_RANGES"),
    ({"name": "A", "week": {"mon": [{"start": "9am", "end": "11:00"}]}}, "INVALID_TIME"),
    ({"name": "A", "kind": "nights"}, "INVALID_SCHEDULE"),
    ({"name": "A", "closed_dates": ["tomorrow"]}, "INVALID_DATE"),
])
def test_bad_schedules_are_422(app_client, body, code):
    r = app_client.post("/api/schedules", headers=LOCAL, json=body)
    assert r.status_code == 422 and r.json()["code"] == code, r.text
    assert app_client.get("/api/schedules", headers=LOCAL).json()["schedules"] == []


def test_not_found_everywhere(app_client):
    for method, path, body in [("get", "/api/schedules/S9", None), ("put", "/api/schedules/S9", {"name": "x"}),
                               ("delete", "/api/schedules/S9", None), ("get", "/api/schedules/S9/slots", None),
                               ("post", "/api/schedules/S9/check", {"text": "kal"}),
                               ("post", "/api/schedules/S9/blocks", {"start": "2026-11-05T10:00", "end": "2026-11-05T11:00"}),
                               ("delete", "/api/schedules/S9/blocks/K1", None)]:
        r = getattr(app_client, method)(path, headers=LOCAL, **({"json": body} if body else {}))
        assert r.status_code == 404 and r.json()["code"] == "SCHEDULE_NOT_FOUND", (method, path, r.text)


# ---------------------------------------------------------------------------------------------- slots and blocks

def test_slots_for_a_week(app_client):
    new(app_client)
    r = app_client.get("/api/schedules/S1/slots?from=2026-11-03&to=2026-11-09", headers=LOCAL)
    out = r.json()
    assert r.status_code == 200 and out["timezone"] == "Asia/Kolkata" and out["kind"] == "slots"
    assert [d["date"] for d in out["days"]] == [f"2026-11-0{i}" for i in range(3, 10)]
    assert [d["open"] for d in out["days"]] == [True] * 5 + [False, True]
    today = out["days"][0]["slots"]
    assert today[0]["status"] == "free" and len(today) == 12 and today[0]["start"] == "2026-11-03T10:00:00+05:30"
    assert set(today[0]) == {"start", "end", "status", "remaining", "bookings", "block_id"}
    assert app_client.get("/api/schedules/S1/slots", headers=LOCAL).json()["days"][0]["date"] == "2026-11-03"   # default week


@pytest.mark.parametrize("query,code", [("from=2026-11-03&to=2026-12-31", "RANGE_TOO_LARGE"),
                                        ("from=2026-11-09&to=2026-11-03", "INVALID_DATE"),
                                        ("from=nov3", "INVALID_DATE")])
def test_slot_range_limits(app_client, query, code):
    new(app_client)
    r = app_client.get(f"/api/schedules/S1/slots?{query}", headers=LOCAL)
    assert r.status_code == 422 and r.json()["code"] == code
    assert app_client.get("/api/schedules/S1/slots?from=2026-11-03&to=2026-12-14", headers=LOCAL).status_code == 200  # 42 days


def test_block_and_unblock_a_slot(app_client):
    new(app_client)
    r = app_client.post("/api/schedules/S1/blocks", headers=LOCAL, json={
        "start": "2026-11-05T10:00:00+05:30", "end": "2026-11-05T10:30:00+05:30", "reason": "Doctor away"})
    block = r.json()["block"]
    assert r.status_code == 200 and block["id"] == "K1" and block["schedule_id"] == "S1" and block["reason"] == "Doctor away"
    day = app_client.get("/api/schedules/S1/slots?from=2026-11-05&to=2026-11-05", headers=LOCAL).json()["days"][0]
    assert day["slots"][0]["status"] == "blocked" and day["slots"][0]["block_id"] == "K1"
    assert day["slots"][0]["remaining"] == 0 and day["slots"][1]["status"] == "free"
    r = check(app_client, "S1", date="2026-11-05", time="10:00")
    assert r["reason"] == "blocked" and r["after"]["start"] == "2026-11-05T10:30:00+05:30"
    assert app_client.delete("/api/schedules/S1/blocks/K1", headers=LOCAL).json() == {"deleted": True}
    assert check(app_client, "S1", date="2026-11-05", time="10:00")["available"]
    gone = app_client.delete("/api/schedules/S1/blocks/K1", headers=LOCAL)
    assert gone.status_code == 404 and gone.json()["code"] == "BLOCK_NOT_FOUND"


def test_a_block_over_a_range_is_one_row_and_bad_blocks_are_422(app_client):
    new(app_client)
    app_client.post("/api/schedules/S1/blocks", headers=LOCAL, json={"start": "2026-11-05T10:00", "end": "2026-11-05T12:00"})
    day = app_client.get("/api/schedules/S1/slots?from=2026-11-05&to=2026-11-05", headers=LOCAL).json()["days"][0]
    assert [s["block_id"] for s in day["slots"][:5]] == ["K1", "K1", "K1", "K1", None]
    for body in ({"start": "2026-11-05T12:00", "end": "2026-11-05T10:00"}, {"start": "soon", "end": "later"}):
        r = app_client.post("/api/schedules/S1/blocks", headers=LOCAL, json=body)
        assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------------------------- check

def test_check_a_free_time_by_fields(app_client):
    new(app_client)
    r = check(app_client, "S1", date="2026-11-05", time="18:00")
    assert r["available"] is True and r["reason"] is None and r["before"] is None and r["after"] is None
    assert set(r) == {"available", "requested", "reason", "before", "after", "spoken"}
    assert r["requested"] == {"start": "2026-11-05T18:00:00+05:30", "end": "2026-11-05T18:30:00+05:30"}
    assert r["spoken"]["en"] == "Thursday at 6 pm is free."


def test_check_thursday_6_pm_when_it_is_taken_gives_both_neighbours(app_client):
    new(app_client)
    assert staff_book(app_client, "S1", "2026-11-05T18:00:00+05:30").status_code == 200
    r = check(app_client, "S1", text="Thursday 6 pm")
    assert r["available"] is False and r["reason"] == "full"
    assert r["before"]["start"] == "2026-11-05T17:30:00+05:30" and r["after"]["start"] == "2026-11-05T18:30:00+05:30"
    assert r["spoken"]["hi"] == "गुरुवार शाम छह बजे वाला समय भरा हुआ है। साढ़े पाँच बजे या साढ़े छह बजे, कौन सा ठीक रहेगा?"
    assert r["spoken"]["en"] == "Thursday at 6 pm is already taken. 5:30 pm or 6:30 pm, which would suit you?"


@pytest.mark.parametrize("text,start", [
    ("is Thursday 6 pm free?", "2026-11-05T18:00:00+05:30"),
    ("kal shaam 6 baje", "2026-11-04T18:00:00+05:30"),
    ("parso subah 10", "2026-11-05T10:00:00+05:30"),
    ("next Monday 4 pm", "2026-11-09T16:00:00+05:30"),
    ("उद्या संध्याकाळी 6 वाजता", "2026-11-04T18:00:00+05:30"),
    ("शुक्रवार सुबह 10 बजे", "2026-11-06T10:00:00+05:30"),
    ("tomorrow at 17:30", "2026-11-04T17:30:00+05:30"),
    ("6 November 11 am", "2026-11-06T11:00:00+05:30"),
])
def test_check_reads_the_callers_words(app_client, text, start):
    new(app_client)
    r = check(app_client, "S1", text=text)
    assert r["requested"]["start"] == start, r
    assert r["available"] is (not text.startswith("next Monday")), r      # 4 pm is outside the hours


def test_explicit_fields_win_over_text_and_loose_words_are_read(app_client):
    new(app_client)
    r = check(app_client, "S1", text="Thursday 6 pm", date="2026-11-06")
    assert r["requested"]["start"] == "2026-11-06T18:00:00+05:30"
    r = check(app_client, "S1", date="kal", time="6 pm")
    assert r["requested"]["start"] == "2026-11-04T18:00:00+05:30"
    assert check(app_client, "S1")["reason"] == "unclear"
    assert check(app_client, "S1", text="blah blah")["reason"] == "unclear"
    off = check(app_client, "S1", text="Thursday 10:10 am")
    assert off["reason"] == "not_a_start" and off["before"] and off["after"] and "We book every" in off["spoken"]["en"]
    for body in ({"date": "someday"}, {"time": "whenever", "date": "2026-11-05"}, {"nights": 0}, {"nights": 99}):
        assert app_client.post("/api/schedules/S1/check", headers=LOCAL, json=body).status_code == 422


def test_check_on_a_closed_day_and_outside_hours(app_client):
    new(app_client)
    sunday = check(app_client, "S1", text="Sunday 11 am")
    assert sunday["reason"] == "closed" and sunday["before"]["start"] == "2026-11-07T19:30:00+05:30"
    assert sunday["after"]["start"] == "2026-11-09T10:00:00+05:30"
    assert "closed on Sunday" in sunday["spoken"]["en"] and sunday["spoken"]["hi"].startswith("रविवार को हम बंद रहते हैं।")
    assert check(app_client, "S1", text="Thursday 3 pm")["reason"] == "outside_hours"
    assert check(app_client, "S1", text="today 9:30")["reason"] == "outside_hours"


def test_check_for_a_stay(app_client):
    hotel(app_client)
    r = check(app_client, "S1", text="2 nights from 12 November")
    assert r["available"] and r["requested"] == {"start": "2026-11-12T12:00:00+05:30", "end": "2026-11-14T11:00:00+05:30"}
    assert "A room is available for 2 nights from" in r["spoken"]["en"]
    again = check(app_client, "S1", date="2026-11-12", nights=2)
    assert again["available"]
    for name in ("Asha", "Ravi"):
        assert staff_book(app_client, "S1", "2026-11-13T12:00:00+05:30", name=name).status_code == 200
    full = check(app_client, "S1", date="2026-11-12", nights=2)
    assert full["reason"] == "full" and full["before"]["start"] == "2026-11-11T12:00:00+05:30"
    assert full["after"]["start"] == "2026-11-14T12:00:00+05:30"


# ---------------------------------------------------------------------------------------------- staff bookings

def test_a_staff_booking_fills_the_slot_and_is_listed(app_client):
    s = new(app_client)
    r = staff_book(app_client, "S1", "2026-11-05T10:30:00+05:30", phone="98765 43210", note="first visit", party=2)
    assert r.status_code == 200, r.text
    b = r.json()["booking"]
    assert b["id"] == "A10" and b["schedule_id"] == "S1" and b["schedule_name"] == s["name"] and b["service"] == s["name"]
    assert b["start"] == "2026-11-05T10:30:00+05:30" and b["end"] == "2026-11-05T11:00:00+05:30"
    assert b["phone"] == "••••3210" and b["note"] == "first visit" and b["party"] == 2 and b["nights"] is None
    assert b["agent_id"] == "agent-1" and b["status"] == "booked" and b["calendar_sync"] == "not_configured"
    day = app_client.get("/api/schedules/S1/slots?from=2026-11-05&to=2026-11-05", headers=LOCAL).json()["days"][0]
    slot = day["slots"][1]
    assert slot["status"] == "full" and [{k: x[k] for k in ("id", "name")} for x in slot["bookings"]] == [{"id": "A10", "name": "Asha Rao"}] and slot["remaining"] == 0
    listed = app_client.get("/api/bookings?schedule_id=S1", headers=LOCAL).json()["bookings"]
    assert [x["id"] for x in listed] == ["A10"]
    assert app_client.get("/api/bookings?schedule_id=S2", headers=LOCAL).json()["bookings"] == []
    assert [x["id"] for x in app_client.get("/api/bookings?agent_id=agent-1", headers=LOCAL).json()["bookings"]] == ["A10"]
    card = app_client.get("/api/schedules?agent_id=agent-1", headers=LOCAL).json()["schedules"][0]["summary"]
    assert card["today_booked"] == 0 and card["next_free"] == "2026-11-03T10:00:00+05:30"


def test_a_slot_cannot_be_booked_twice_and_the_answer_says_what_is_near(app_client):
    new(app_client)
    assert staff_book(app_client, "S1", "2026-11-05T10:30:00+05:30").status_code == 200
    r = staff_book(app_client, "S1", "2026-11-05T10:30:00+05:30", name="Ravi")
    assert r.status_code == 409 and r.json()["code"] == "SLOT_UNAVAILABLE"
    d = r.json()["details"]
    assert d["reason"] == "full" and d["before"]["start"] == "2026-11-05T10:00:00+05:30"
    assert d["after"]["start"] == "2026-11-05T11:00:00+05:30" and d["spoken"]["en"]
    assert len(app_client.get("/api/bookings", headers=LOCAL).json()["bookings"]) == 1


def test_capacity_allows_more_than_one(app_client):
    new(app_client, capacity=2)
    for name, code in (("A One", 200), ("B Two", 200), ("C Three", 409)):
        assert staff_book(app_client, "S1", "2026-11-05T10:30:00+05:30", name=name).status_code == code
    slot = app_client.get("/api/schedules/S1/slots?from=2026-11-05&to=2026-11-05", headers=LOCAL).json()["days"][0]["slots"][1]
    assert slot["status"] == "full" and len(slot["bookings"]) == 2


def test_staff_may_book_inside_the_notice_but_not_a_blocked_closed_or_past_time(app_client):
    new(app_client)
    assert staff_book(app_client, "S1", "2026-11-03T10:00:00+05:30").status_code == 200       # an hour away, fine
    app_client.post("/api/schedules/S1/blocks", headers=LOCAL, json={"start": "2026-11-05T10:00", "end": "2026-11-05T10:30"})
    for start, reason in (("2026-11-05T10:00:00+05:30", "blocked"), ("2026-11-08T11:00:00+05:30", "closed"),
                          ("2026-11-02T10:00:00+05:30", "past"), ("2026-11-05T14:00:00+05:30", "outside_hours"),
                          ("2026-11-05T10:10:00+05:30", "not_a_start")):
        r = staff_book(app_client, "S1", start, name="Late Comer")
        assert r.status_code == 409 and r.json()["details"]["reason"] == reason, (start, r.text)


@pytest.mark.parametrize("body,code", [
    ({"name": "x"}, "INVALID_NAME"), ({"name": "Asha 123"}, "INVALID_NAME"), ({"phone": "12345"}, "INVALID_PHONE"),
    ({"party": 0}, "INVALID_PARTY"), ({"start": "tomorrow ten"}, "INVALID_DATETIME"),
])
def test_staff_booking_validation(app_client, body, code):
    new(app_client)
    r = app_client.post("/api/bookings", headers=LOCAL, json={"schedule_id": "S1", "start": "2026-11-05T10:30:00+05:30",
                                                              "name": "Asha Rao", **body})
    assert r.status_code == 422 and r.json()["code"] == code, r.text


def test_staff_booking_for_an_unknown_schedule_is_404(app_client):
    r = staff_book(app_client, "S9", "2026-11-05T10:30:00+05:30")
    assert r.status_code == 404 and r.json()["code"] == "SCHEDULE_NOT_FOUND"


def test_a_stay_booking_and_cancelling_frees_it(app_client):
    hotel(app_client)
    r = staff_book(app_client, "S1", "2026-11-12T00:00:00+05:30", nights=3, phone="9876543210")
    b = r.json()["booking"]
    assert r.status_code == 200 and b["nights"] == 3 and b["start"] == "2026-11-12T12:00:00+05:30"
    assert b["end"] == "2026-11-15T11:00:00+05:30"
    week = app_client.get("/api/schedules/S1/slots?from=2026-11-11&to=2026-11-16", headers=LOCAL).json()["days"]
    assert [d["slots"][0]["status"] for d in week] == ["free", "booked", "booked", "booked", "free", "free"]
    assert [d["slots"][0]["remaining"] for d in week] == [2, 1, 1, 1, 2, 2]
    assert week[1]["slots"][0]["start"] == "2026-11-12T12:00:00+05:30" and week[1]["slots"][0]["end"] == "2026-11-13T11:00:00+05:30"
    for n in (0, 61):
        assert staff_book(app_client, "S1", "2026-11-20T12:00:00+05:30", nights=n).status_code == 422
    assert app_client.delete(f"/api/bookings/{b['id']}", headers=LOCAL).json()["booking"]["status"] == "cancelled"
    week = app_client.get("/api/schedules/S1/slots?from=2026-11-12&to=2026-11-12", headers=LOCAL).json()["days"]
    assert week[0]["slots"][0]["status"] == "free"


def test_a_doctor_booking_of_the_older_tool_still_lists_with_the_new_fields(app_client, monkeypatch):
    from mcp_servers import appointments as ap
    monkeypatch.setattr(ap, "now", lambda: NOW)
    ap.book_appointment(name="Priya Sharma", phone="9876543210", date="2026-11-09", time="10:30")
    [b] = app_client.get("/api/bookings", headers=LOCAL).json()["bookings"]
    assert b["service"] == "Dr. Meera Kulkarni" and b["schedule_id"] is None and b["schedule_name"] is None
    assert b["start"] == "2026-11-09T10:30:00+05:30" and b["end"] == "2026-11-09T11:00:00+05:30"
    assert b["nights"] is None and b["party"] is None
    assert app_client.get("/api/bookings?schedule_id=S1", headers=LOCAL).json()["bookings"] == []


# ---------------------------------------------------------------------------------------------- deleting

def test_deleting_a_schedule_with_future_bookings_needs_force(app_client):
    new(app_client)
    staff_book(app_client, "S1", "2026-11-05T10:30:00+05:30")
    staff_book(app_client, "S1", "2026-11-05T11:00:00+05:30", name="Ravi Kumar")
    app_client.post("/api/schedules/S1/blocks", headers=LOCAL, json={"start": "2026-11-06T10:00", "end": "2026-11-06T11:00"})
    r = app_client.delete("/api/schedules/S1", headers=LOCAL)
    assert r.status_code == 409 and r.json()["code"] == "HAS_BOOKINGS" and r.json()["details"] == {"count": 2}
    assert app_client.get("/api/schedules/S1", headers=LOCAL).status_code == 200
    r = app_client.delete("/api/schedules/S1?force=1", headers=LOCAL)
    assert r.status_code == 200 and r.json() == {"deleted": True, "cancelled_bookings": 2}
    assert app_client.get("/api/schedules/S1", headers=LOCAL).status_code == 404
    assert {b["status"] for b in app_client.get("/api/bookings", headers=LOCAL).json()["bookings"]} == {"cancelled"}
    assert sch.list_blocks("S1") == []


def test_a_schedule_without_future_bookings_deletes_plainly(app_client):
    new(app_client)
    assert app_client.delete("/api/schedules/S1", headers=LOCAL).json() == {"deleted": True, "cancelled_bookings": 0}


def test_timezone_follows_the_schedule(app_client, monkeypatch):
    new(app_client, timezone="Asia/Dubai")
    out = app_client.get("/api/schedules/S1/slots?from=2026-11-05&to=2026-11-05", headers=LOCAL).json()
    assert out["timezone"] == "Asia/Dubai" and out["days"][0]["slots"][0]["start"] == "2026-11-05T10:00:00+04:00"


def test_two_bookings_of_the_last_place_race_and_only_one_wins(app_client):
    import threading
    new(app_client)
    results = []

    def go(name):
        results.append(staff_book(app_client, "S1", "2026-11-05T10:30:00+05:30", name=name).status_code)

    threads = [threading.Thread(target=go, args=(n,)) for n in ("Asha", "Ravi", "Neha", "Omar", "Kiran", "Dilip")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [200, 409, 409, 409, 409, 409]
    assert len(app_client.get("/api/bookings", headers=LOCAL).json()["bookings"]) == 1
