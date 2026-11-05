"""Book, move and cancel over three calls, through the real tool path: services/schedule_tools.run wrapped per call by
services/turn_pipeline._observed (which ties each change to its call), the Google Calendar event that follows the booking
(services/gcal.py faked), and the WhatsApp message after each hang-up (Meta faked in services/whatsapp._http)."""
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from services import booking_notify as bn, gcal, schedule_tools as st, schedules as sch, turn_pipeline as tp, whatsapp

NOW = datetime(2026, 11, 3, 9, 0)    # Tuesday
AGENT = "agent-e2e"
PHONE = "9876543210"                 # the synthetic number the other tests use


class Calendar:
    def __init__(self):
        self.events = {}

    def create_event(self, summary, description, start, minutes, location="", attendees=None, request_id=""):
        event_id = f"evt{len(self.events) + 1}"
        self.events[event_id] = start
        return event_id

    def move_event(self, event_id, start, minutes, description=None):
        self.events[event_id] = start

    def delete_event(self, event_id):
        self.events.pop(event_id, None)


@pytest.fixture()
def desk(monkeypatch):
    monkeypatch.setattr(sch, "now", lambda tz=None: NOW)
    week = {d: [{"start": "10:00", "end": "13:00"}, {"start": "15:00", "end": "20:00"}]
            for d in ("mon", "tue", "wed", "thu", "fri", "sat")} | {"sun": []}
    sch.create({"name": "Doctor consultations", "slot_minutes": 60, "week": week, "agent_id": AGENT})
    cal = Calendar()
    monkeypatch.setattr(gcal, "configured", lambda: True)
    monkeypatch.setattr(gcal, "day_busy", lambda d, tz=None: [])
    for name in ("create_event", "move_event", "delete_event"):
        monkeypatch.setattr(gcal, name, getattr(cal, name))
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "EAAB-test-token-e2e")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "100200300")
    sent = []

    def meta(method, url, *, headers=None, json_body=None):
        sent.append(json_body)
        return 200, {"messages": [{"id": f"wamid.{len(sent)}"}]}
    monkeypatch.setattr(whatsapp, "_http", meta)
    return SimpleNamespace(calendar=cal, sent=sent)


def on_call(cid):
    """A tool runner as a live call sees it: every scheduling tool bound to call `cid`."""
    run = SimpleNamespace(conversation_id=cid, emit_tool_start=lambda tool, args: None)
    spec = tp._observed(SimpleNamespace(runner=lambda tool, args: st.run(tool, args, AGENT)), run)
    return spec.runner


def hang_up(cid):
    bn.call_ended(cid)
    bn.flush_due(time.time() + 31)


def texts():
    return [r["text"] for r in reversed(bn.recent_messages()) if r["state"] == "sent"]


def test_book_move_cancel_over_three_calls_keeps_calendar_and_whatsapp_in_step(desk):
    # Call 1: book Thursday 3 pm; the caller agrees to WhatsApp when asked.
    tool = on_call("call-1")
    ok, booked = tool("book", {"date": "2026-11-05", "time": "15:00", "name": "Ravi", "phone": PHONE})
    assert ok and booked["whatsapp"]["status"] == "ask"
    assert tool("whatsapp_consent", {"agreed": True})[0]
    bid = booked["booking_id"]
    assert list(desk.calendar.events.values()) == [datetime(2026, 11, 5, 15, 0)]
    assert desk.sent == []                                   # nothing while the call is on
    hang_up("call-1")
    assert len(desk.sent) == 1 and "is confirmed" in texts()[-1] and "3:00 pm" in texts()[-1]

    # Call 2: move it to 4 pm. Not asked again; the same calendar event moves.
    tool = on_call("call-2")
    ok, moved = tool("reschedule", {"booking_id": bid, "date": "2026-11-05", "time": "16:00"})
    assert ok and moved.get("whatsapp", {}).get("status") == "queued"
    assert list(desk.calendar.events.values()) == [datetime(2026, 11, 5, 16, 0)]
    hang_up("call-2")
    assert len(desk.sent) == 2 and "moved from" in texts()[-1] and "3:00 pm" in texts()[-1] and "4:00 pm" in texts()[-1]

    # Call 3: cancel. The event goes; the cancellation names the 4 pm.
    tool = on_call("call-3")
    ok, _ = tool("cancel", {"booking_id": bid})
    assert ok and desk.calendar.events == {}
    hang_up("call-3")
    hang_up("call-3")                                         # a second hang-up report sends nothing more
    assert len(desk.sent) == 3 and "has been cancelled" in texts()[-1] and "4:00 pm" in texts()[-1]
    assert all(b["to"] == "91" + PHONE for b in desk.sent)
