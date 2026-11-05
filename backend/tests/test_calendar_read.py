"""The owner's own Google Calendar events block times (services/gcal.day_busy, availability.Engine external, docs/CALENDAR.md).
Only services/gcal._http is faked; nothing talks to Google."""
from datetime import date, datetime

import pytest

from mcp_servers import appointments as ap
from services import availability as av, gcal, schedule_bookings as sb, schedule_tools as st, schedules as sch

NOW = datetime(2026, 11, 3, 9, 0)    # Tuesday
THURSDAY = "2026-11-05"
AGENT = "agent-1"


def ev(start, end, **kw):
    return {"status": "confirmed", "start": {"dateTime": start}, "end": {"dateTime": end}, **kw}


class Google:
    """A fake events.list: answers with `items`, counts the requests, can fail or be slow."""

    def __init__(self):
        self.items = []
        self.requests = []
        self.error = None

    def http(self, method, url, *, headers=None, params=None, json_body=None, data=None, timeout=None):
        self.requests.append({"method": method, "url": url, "params": params, "timeout": timeout})
        if self.error:
            raise self.error
        return 200, {"items": list(self.items)}


@pytest.fixture()
def google(monkeypatch):
    g = Google()
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "owner@example.com")
    monkeypatch.setattr(gcal, "configured", lambda: True)
    monkeypatch.setattr(gcal, "_access_token", lambda timeout=None: "token")
    monkeypatch.setattr(gcal, "_http", g.http)
    clock = {"t": 1000.0}
    monkeypatch.setattr(gcal, "_clock", lambda: clock["t"])
    g.clock = clock
    gcal.reset_busy()
    yield g
    gcal.reset_busy()


@pytest.fixture()
def clinic(monkeypatch):
    monkeypatch.setattr(sch, "now", lambda tz=None: NOW)
    week = {d: [{"start": "10:00", "end": "13:00"}, {"start": "15:00", "end": "20:00"}] for d in sch.DAYS if d != "sun"} | {"sun": []}
    return sch.create({"name": "Doctor consultations", "slot_minutes": 60, "week": week, "agent_id": AGENT})


# ---------------------------------------------------------------------------------------------- reading the events

def test_timed_events_become_local_busy_intervals(google):
    google.items = [ev("2026-11-05T15:00:00+05:30", "2026-11-05T16:30:00+05:30"),
                    ev("2026-11-05T11:00:00Z", "2026-11-05T11:30:00Z")]       # 16:30 to 17:00 in Kolkata
    assert gcal.day_busy(date(2026, 11, 5)) == [(datetime(2026, 11, 5, 15, 0), datetime(2026, 11, 5, 16, 30)),
                                               (datetime(2026, 11, 5, 16, 30), datetime(2026, 11, 5, 17, 0))]
    req = google.requests[0]
    assert req["method"] == "GET" and req["url"].endswith("/calendars/owner%40example.com/events")
    assert req["params"]["singleEvents"] == "true" and req["params"]["orderBy"] == "startTime"
    assert req["timeout"] == 2.0


def test_events_that_block_nothing_are_skipped(google):
    google.items = [
        ev("2026-11-05T10:00:00+05:30", "2026-11-05T11:00:00+05:30", status="cancelled"),
        ev("2026-11-05T11:00:00+05:30", "2026-11-05T12:00:00+05:30", transparency="transparent"),
        ev("2026-11-05T12:00:00+05:30", "2026-11-05T13:00:00+05:30", extendedProperties={"private": {"vakyaRequest": "A10"}}),
        ev("2026-11-05T15:00:00+05:30", "2026-11-05T16:00:00+05:30",
           attendees=[{"self": True, "responseStatus": "declined"}]),
        {"status": "confirmed", "start": {"date": "2026-11-05"}, "end": {"date": "2026-11-06"}, "transparency": "transparent"},
        ev("2026-11-05T17:00:00+05:30", "2026-11-05T18:00:00+05:30"),
    ]
    assert gcal.day_busy(date(2026, 11, 5)) == [(datetime(2026, 11, 5, 17, 0), datetime(2026, 11, 5, 18, 0))]


def test_an_all_day_event_blocks_the_day_only_when_shown_as_busy(google):
    google.items = [{"status": "confirmed", "summary": "Out of office", "start": {"date": "2026-11-05"}, "end": {"date": "2026-11-06"}}]
    assert gcal.day_busy(date(2026, 11, 5)) == [(datetime(2026, 11, 5, 0, 0), datetime(2026, 11, 6, 0, 0))]
    assert gcal.day_busy(date(2026, 11, 6)) == []


def test_an_event_across_midnight_is_cut_at_the_days(google):
    google.items = [ev("2026-11-05T22:00:00+05:30", "2026-11-06T02:00:00+05:30")]
    assert gcal.day_busy(date(2026, 11, 5)) == [(datetime(2026, 11, 5, 22, 0), datetime(2026, 11, 6, 0, 0))]
    assert gcal.day_busy(date(2026, 11, 6)) == [(datetime(2026, 11, 6, 0, 0), datetime(2026, 11, 6, 2, 0))]


def test_a_day_is_remembered_for_sixty_seconds_and_a_window_costs_one_request(google):
    google.items = [ev("2026-11-05T15:00:00+05:30", "2026-11-05T16:00:00+05:30")]
    gcal.day_busy(date(2026, 11, 5))
    gcal.day_busy(date(2026, 11, 5))
    gcal.day_busy(date(2026, 11, 9))                   # inside the same window
    assert len(google.requests) == 1
    google.clock["t"] += 59
    gcal.day_busy(date(2026, 11, 5))
    assert len(google.requests) == 1
    google.clock["t"] += 2
    gcal.day_busy(date(2026, 11, 5))
    assert len(google.requests) == 2


def test_a_change_through_vakya_forgets_its_day(google):
    google.items = [ev("2026-11-05T15:00:00+05:30", "2026-11-05T16:00:00+05:30")]
    gcal.day_busy(date(2026, 11, 5))
    gcal.invalidate(date(2026, 11, 5))
    google.items = []
    assert gcal.day_busy(date(2026, 11, 5)) == []
    assert len(google.requests) == 2


def test_a_failure_is_remembered_so_callers_do_not_each_wait(google):
    google.error = gcal.CalendarError("Could not reach Google Calendar (ReadTimeout).")
    for _ in range(3):
        with pytest.raises(gcal.CalendarError):
            gcal.day_busy(date(2026, 11, 5))
    assert len(google.requests) == 1
    google.clock["t"] += 16
    google.error = None
    assert gcal.day_busy(date(2026, 11, 5)) == [] and len(google.requests) == 2


# ---------------------------------------------------------------------------------------------- availability

def test_an_event_on_the_owners_calendar_blocks_the_slot_with_its_own_reason(clinic, google):
    google.items = [ev("2026-11-05T15:30:00+05:30", "2026-11-05T16:30:00+05:30", summary="Lunch with a supplier")]
    engine = sb.engine_for(clinic)
    res = engine.check(day=date(2026, 11, 5), at="16:00", caller=True)
    assert res["available"] is False and res["reason"] == "calendar_busy"
    assert res["spoken"]["en"].startswith("We are busy Thursday at 4 pm.") and "व्यस्त" in res["spoken"]["hi"]
    assert res["before"] and res["after"]                      # the nearest free times, as for any other reason
    assert engine.check(day=date(2026, 11, 5), at="15:00", caller=True)["reason"] == "calendar_busy"   # 15:00 to 16:00 overlaps 15:30
    assert engine.check(day=date(2026, 11, 5), at="17:00", caller=True)["available"] is True
    blocked = [s for s in engine.slots(date(2026, 11, 5)) if s.calendar]
    assert [s.start.hour for s in blocked] == [15, 16] and all(s.status == "blocked" and s.block_id is None for s in blocked)
    assert engine.day_public(date(2026, 11, 5))["slots"][3]["calendar"] is True


def test_the_tools_and_the_desk_see_it(clinic, google, app_client):
    google.items = [ev("2026-11-05T16:00:00+05:30", "2026-11-05T17:00:00+05:30")]
    ok, v = st.run("check_availability", {"date": THURSDAY, "time": "16:00"}, AGENT)
    assert ok and v["available"] is False and v["reason"] == "calendar_busy" and v["alternatives"]
    ok, v = st.run("list_free_slots", {"date": THURSDAY}, AGENT)
    assert "16:00" not in v["free"] and "15:00" in v["free"]
    ok, v = st.run("book", {"date": THURSDAY, "time": "16:00", "name": "Priya Sharma", "phone": "9876543210"}, AGENT)
    assert not ok and v["reason"] == "calendar_busy"
    r = app_client.post("/api/bookings", json={"schedule_id": clinic["id"], "start": f"{THURSDAY}T16:00", "name": "Priya Sharma"},
                        headers={"storage-mode": "local"})
    assert r.status_code == 409 and r.json()["details"]["reason"] == "calendar_busy"
    r = app_client.get(f"/api/schedules/{clinic['id']}/slots?from={THURSDAY}&to={THURSDAY}", headers={"storage-mode": "local"})
    assert r.status_code == 200 and any(s.get("calendar") for s in r.json()["days"][0]["slots"])


def test_a_move_cannot_land_on_an_owner_event_but_an_own_event_does_not_block_itself(clinic, google, monkeypatch):
    monkeypatch.setattr(gcal, "create_event", lambda *a, **k: "evt1")
    monkeypatch.setattr(gcal, "move_event", lambda *a, **k: None)
    sb.create(clinic, day=date(2026, 11, 5), at="15:00", name="Priya Sharma", phone="9876543210", caller=True, source="agent", now=NOW)
    # Vakya's own event for A10 is on the calendar, and the owner has a lunch at 16:00
    google.items = [ev("2026-11-05T15:00:00+05:30", "2026-11-05T16:00:00+05:30", extendedProperties={"private": {"vakyaRequest": "A10"}}),
                    ev("2026-11-05T16:00:00+05:30", "2026-11-05T17:00:00+05:30")]
    gcal.invalidate()
    with pytest.raises(sb.BookingRefused) as e:
        sb.reschedule("A10", AGENT, day=date(2026, 11, 5), at="16:00", caller=True, now=NOW)
    assert e.value.result["reason"] == "calendar_busy"
    new, _ = sb.reschedule("A10", AGENT, day=date(2026, 11, 5), at="17:00", caller=True, now=NOW)
    assert new["time"] == "17:00"


def test_a_booking_through_vakya_forgets_the_day(clinic, google, monkeypatch):
    monkeypatch.setattr(gcal, "create_event", lambda *a, **k: "evt1")
    gcal.day_busy(date(2026, 11, 5))
    n = len(google.requests)
    sb.create(clinic, day=date(2026, 11, 5), at="15:00", name="Priya Sharma", phone="9876543210", caller=True, source="agent", now=NOW)
    gcal.day_busy(date(2026, 11, 5))
    assert len(google.requests) > n


def test_when_google_fails_vakyas_own_list_decides_and_nothing_fails(clinic, google, caplog):
    google.error = gcal.CalendarError("Could not reach Google Calendar (ReadTimeout).")
    with caplog.at_level("WARNING"):
        ok, v = st.run("book", {"date": THURSDAY, "time": "16:00", "name": "Priya Sharma", "phone": "9876543210"}, AGENT)
    assert ok and v["time"] == "16:00"
    assert any("could not read the owner's events" in r.message for r in caplog.records)
    ok, v = st.run("check_availability", {"date": THURSDAY, "time": "17:00"}, AGENT)
    assert ok and v["available"] is True


def test_an_engine_asks_google_once_per_failure_not_once_per_day(clinic, monkeypatch):
    calls = []

    def broken(day):
        calls.append(day)
        raise gcal.CalendarError("down")
    engine = av.Engine(clinic, [], [], NOW, external=broken)
    engine.check(day=date(2026, 11, 5), at="18:00")
    engine.next_free()
    assert len(calls) == 1


def test_without_google_nothing_changes(clinic, monkeypatch):
    monkeypatch.setattr(gcal, "day_busy", lambda *a, **k: pytest.fail("no calendar is set up"))
    assert av.calendar_source(clinic) is None
    res = sb.engine_for(clinic).check(day=date(2026, 11, 5), at="16:00", caller=True)
    assert res["available"] is True


def test_a_stay_is_not_blocked_by_a_meeting(monkeypatch, google):
    monkeypatch.setattr(sch, "now", lambda tz=None: NOW)
    rooms = sch.create({"name": "Rooms", "kind": "nights", "capacity": 2, "check_in": "14:00", "check_out": "11:00",
                        "week": {d: [{"start": "00:00", "end": "24:00"}] for d in sch.DAYS}, "agent_id": AGENT})
    google.items = [{"status": "confirmed", "start": {"date": "2026-11-10"}, "end": {"date": "2026-11-11"}}]
    assert av.calendar_source(rooms) is None
    assert sb.engine_for(rooms).check(day=date(2026, 11, 10), nights=1)["available"] is True


def test_the_move_event_call_patches_the_event(google, monkeypatch):
    seen = {}

    def http(method, url, *, headers=None, params=None, json_body=None, data=None, timeout=None):
        seen.update(method=method, url=url, body=json_body)
        return 200, {}
    monkeypatch.setattr(gcal, "_http", http)
    gcal.move_event("evt1", datetime(2026, 11, 5, 16, 0), 30, "Moved from Thu 5 Nov 3:00 pm")
    assert seen["method"] == "PATCH" and seen["url"].endswith("/events/evt1")
    assert seen["body"]["start"]["dateTime"].startswith("2026-11-05T16:00:00") and seen["body"]["end"]["dateTime"].startswith("2026-11-05T16:30:00")
    assert seen["body"]["description"] == "Moved from Thu 5 Nov 3:00 pm"

    def gone(*a, **k):
        return 404, {"error": {"message": "Not Found"}}
    monkeypatch.setattr(gcal, "_http", gone)
    with pytest.raises(gcal.CalendarError) as e:
        gcal.move_event("evt1", datetime(2026, 11, 5, 16, 0), 30)
    assert e.value.status == 404
