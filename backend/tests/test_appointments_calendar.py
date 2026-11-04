"""Google Calendar sync of the appointments tool (mcp_servers/appointments.py): a booking creates an event, a cancellation
removes it, and a calendar that fails never breaks the booking. services/gcal.py is faked; nothing talks to Google."""
import threading
import time
from datetime import datetime

import pytest

from mcp_servers import appointments as ap
from services import gcal

LOCAL = {"storage-mode": "local"}
NOW = datetime(2026, 11, 4, 9, 0)   # a Wednesday
MONDAY = "2026-11-09"


class FakeCalendar:
    def __init__(self):
        self.created, self.deleted = [], []
        self.fail_create = None
        self.fail_delete = None
        self.gate = None          # a threading.Event the create call waits for (a slow Google)

    def create_event(self, summary, description, start, minutes, location="", attendees=None, request_id=""):
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail_create:
            raise self.fail_create
        self.created.append({"summary": summary, "description": description, "start": start, "minutes": minutes,
                             "request_id": request_id})
        return f"evt{len(self.created)}"

    def delete_event(self, event_id):
        if self.fail_delete:
            raise self.fail_delete
        self.deleted.append(event_id)


@pytest.fixture()
def cal(monkeypatch, tmp_path):
    monkeypatch.setenv("VAKYA_APPOINTMENTS_FILE", str(tmp_path / "mcp" / "appointments.json"))
    monkeypatch.setattr(ap, "now", lambda: NOW)
    fake = FakeCalendar()
    monkeypatch.setattr(gcal, "configured", lambda: True)
    monkeypatch.setattr(gcal, "create_event", fake.create_event)
    monkeypatch.setattr(gcal, "delete_event", fake.delete_event)
    return fake


def book(**kw):
    args = {"name": "Priya Sharma", "phone": "98765 43210", "date": MONDAY, "time": "10:30", "reason": "fever"}
    args.update(kw)
    return ap.book_appointment(**args)


def stored(booking_id="A10"):
    return next(b for b in ap.all_bookings() if b["booking_id"] == booking_id)


def wait_for(predicate, seconds=3):
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_a_booking_becomes_a_calendar_event(cal):
    b = book()
    assert b["booking_id"] == "A10"
    [event] = cal.created
    assert event["start"] == datetime(2026, 11, 9, 10, 30) and event["minutes"] == 30 and event["request_id"] == "A10"
    assert "Priya Sharma" in event["summary"] and "Dr. Meera Kulkarni" in event["summary"]
    assert "fever" in event["description"] and "A10" in event["description"]
    row = stored()
    assert row["calendar_sync"] == "synced" and row["calendar_event_id"] == "evt1"


def test_nothing_is_sent_when_no_calendar_is_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("VAKYA_APPOINTMENTS_FILE", str(tmp_path / "mcp" / "appointments.json"))
    monkeypatch.setattr(ap, "now", lambda: NOW)
    monkeypatch.delenv("GOOGLE_CALENDAR_ID", raising=False)
    monkeypatch.setattr(gcal, "create_event", lambda *a, **k: pytest.fail("no calendar is set up"))
    book()
    assert "calendar_sync" not in stored() and not ap.calendar_configured()


def test_a_failing_calendar_never_breaks_the_booking(cal):
    cal.fail_create = gcal.CalendarError("Google Calendar said 403: share the calendar with the service account")
    b = book()
    assert b["booking_id"] == "A10" and b["status"] == "booked"
    row = stored()
    assert row["status"] == "booked" and row["calendar_sync"] == "failed" and "403" in row["calendar_error"]
    assert book(time="11:00")["booking_id"] == "A11"        # and the next booking works


def test_an_unexpected_calendar_error_is_also_contained(cal):
    cal.fail_create = RuntimeError("boom")
    assert book()["booking_id"] == "A10"
    assert stored()["calendar_sync"] == "failed"


def test_a_slow_google_does_not_hold_up_the_caller(cal, monkeypatch):
    monkeypatch.setattr(ap, "CALENDAR_WAIT_S", 0.1)
    cal.gate = threading.Event()
    t0 = time.time()
    book()
    assert time.time() - t0 < 1.0                            # the tool answered at once
    assert stored()["calendar_sync"] == "pending"
    cal.gate.set()
    assert wait_for(lambda: stored()["calendar_sync"] == "synced")
    assert stored()["calendar_event_id"] == "evt1"


def test_cancelling_removes_the_event(cal):
    book()
    out = ap.cancel_appointment("A10")
    assert out["status"] == "cancelled" and cal.deleted == ["evt1"]
    row = stored()
    assert row["calendar_sync"] == "synced" and "calendar_event_id" not in row


def test_a_failing_delete_still_cancels(cal):
    book()
    cal.fail_delete = gcal.CalendarError("Google Calendar said 500")
    assert ap.cancel_appointment("A10")["status"] == "cancelled"
    row = stored()
    assert row["status"] == "cancelled" and row["calendar_sync"] == "failed" and row["calendar_event_id"] == "evt1"


def test_cancelling_a_booking_that_never_reached_the_calendar_deletes_nothing(cal):
    cal.fail_create = gcal.CalendarError("down")
    book()
    cal.fail_create = None
    ap.cancel_appointment("A10")
    assert cal.deleted == []


def test_an_event_made_while_the_booking_is_cancelled_is_taken_off_again(cal, monkeypatch):
    real = cal.create_event

    def create_then_cancel(*a, **k):
        event = real(*a, **k)
        ap.cancel_booking("A10")                              # the desk cancels while Google is answering
        return event

    monkeypatch.setattr(gcal, "create_event", create_then_cancel)
    book()
    assert cal.deleted == ["evt1"] and stored()["status"] == "cancelled"


# ------------------------------------------------------------------------------------------------- the desk API

def test_the_bookings_api_shows_the_calendar_state(app_client, cal):
    book()
    book(time="11:00")
    cal.fail_create = gcal.CalendarError("nope")
    book(time="11:30")
    rows = {b["id"]: b["calendar_sync"] for b in app_client.get("/api/bookings", headers=LOCAL).json()["bookings"]}
    assert rows == {"A10": "synced", "A11": "synced", "A12": "failed"}


def test_the_state_is_not_configured_without_a_calendar(app_client, monkeypatch, tmp_path):
    monkeypatch.setenv("VAKYA_APPOINTMENTS_FILE", str(tmp_path / "mcp" / "appointments.json"))
    monkeypatch.setattr(ap, "now", lambda: NOW)
    monkeypatch.setattr(gcal, "configured", lambda: False)
    book()
    [b] = app_client.get("/api/bookings", headers=LOCAL).json()["bookings"]
    assert b["calendar_sync"] == "not_configured"


def test_cancelling_from_the_desk_removes_the_event_and_survives_a_calendar_failure(app_client, cal):
    book()
    book(time="11:00")
    r = app_client.delete("/api/bookings/A10", headers=LOCAL)
    assert r.status_code == 200 and r.json()["booking"]["status"] == "cancelled" and cal.deleted == ["evt1"]
    cal.fail_delete = gcal.CalendarError("Google Calendar said 500")
    r = app_client.delete("/api/bookings/A11", headers=LOCAL)
    assert r.status_code == 200 and r.json()["booking"]["status"] == "cancelled"
    assert r.json()["booking"]["calendar_sync"] == "failed"
