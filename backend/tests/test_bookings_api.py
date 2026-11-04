"""GET /api/bookings and DELETE /api/bookings/{id}: the real appointments of the appointments store, with a temp file."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from mcp_servers import appointments as ap

LOCAL = {"storage-mode": "local"}
NOW = datetime(2026, 11, 4, 9, 0)  # a Wednesday
MONDAY, TUESDAY = "2026-11-09", "2026-11-10"


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("VAKYA_APPOINTMENTS_FILE", str(tmp_path / "mcp" / "appointments.json"))
    monkeypatch.delenv("VAKYA_TIMEZONE", raising=False)
    monkeypatch.delenv("GOOGLE_CALENDAR_ID", raising=False)
    monkeypatch.setattr(ap, "now", lambda: NOW)
    return tmp_path / "mcp" / "appointments.json"


def book(**kw):
    args = {"name": "Priya Sharma", "phone": "98765 43210", "date": MONDAY, "time": "10:30"}
    args.update(kw)
    return ap.book_appointment(**args)


def get(app_client, query=""):
    r = app_client.get(f"/api/bookings{query}", headers=LOCAL)
    assert r.status_code == 200, r.text
    return r.json()


def test_empty_store_lists_nothing(app_client):
    out = get(app_client)
    assert out["bookings"] == [] and out["timezone"] == "Asia/Kolkata"


def test_a_booking_has_the_documented_shape(app_client):
    book(reason="fever")
    [b] = get(app_client)["bookings"]
    assert b["id"] == "A10" and b["name"] == "Priya Sharma"
    assert b["phone"] == "••••3210" and "98765" not in str(b)
    assert b["service"] == "Dr. Meera Kulkarni" and b["specialty"] == "General Physician" and b["reason"] == "fever"
    assert b["start"] == "2026-11-09T10:30:00+05:30" and b["timezone"] == "Asia/Kolkata"
    assert b["status"] == "booked" and b["calendar_sync"] == "not_configured"
    assert b["conversation_id"] is None and b["receipt_id"] is None
    assert b["created_at"] == "2026-11-04T09:00:00+05:30"


def test_the_timezone_follows_the_setting(app_client, monkeypatch):
    monkeypatch.setenv("VAKYA_TIMEZONE", "Asia/Dubai")
    book()
    out = get(app_client)
    assert out["timezone"] == "Asia/Dubai" and out["bookings"][0]["start"].endswith("+04:00")


def test_from_to_and_agent_filters(app_client):
    book(time="10:00")                                   # Monday 9 Nov
    book(date=TUESDAY, doctor="Rohan", time="16:00")     # Tuesday 10 Nov
    ap.annotate("A11", agent_id="agent-1")
    assert [b["id"] for b in get(app_client)["bookings"]] == ["A10", "A11"]  # soonest first
    assert [b["id"] for b in get(app_client, f"?from={TUESDAY}")["bookings"]] == ["A11"]
    assert [b["id"] for b in get(app_client, f"?to={MONDAY}")["bookings"]] == ["A10"]
    assert [b["id"] for b in get(app_client, f"?from={MONDAY}&to={TUESDAY}")["bookings"]] == ["A10", "A11"]
    assert [b["id"] for b in get(app_client, "?agent_id=agent-1")["bookings"]] == ["A11"]
    assert get(app_client, "?agent_id=nobody")["bookings"] == []


@pytest.mark.parametrize("query", ["?from=tomorrow", "?to=2026-13-40", "?from=2026-11-10&to=2026-11-01"])
def test_bad_dates_are_rejected(app_client, query):
    r = app_client.get(f"/api/bookings{query}", headers=LOCAL)
    assert r.status_code == 422 and r.json()["code"] == "INVALID_DATE"


def test_conversation_and_receipt_links_are_kept_once(app_client):
    book()
    assert ap.annotate("A10", conversation_id="conv-1", receipt_id="rcpt-1", agent_id="agent-1") is True
    assert ap.annotate("A10", conversation_id="conv-2") is True      # never overwritten
    assert ap.annotate("A99", conversation_id="x") is False
    [b] = get(app_client)["bookings"]
    assert (b["conversation_id"], b["receipt_id"], b["agent_id"]) == ("conv-1", "rcpt-1", "agent-1")


def test_cancel_marks_the_booking_and_frees_the_slot(app_client):
    book()
    r = app_client.delete("/api/bookings/A10", headers=LOCAL)
    assert r.status_code == 200 and r.json()["booking"]["status"] == "cancelled"
    assert get(app_client)["bookings"][0]["status"] == "cancelled"   # still listed, marked
    assert book()["booking_id"] == "A11"                              # the slot is free again


def test_cancel_unknown_and_twice(app_client):
    assert app_client.delete("/api/bookings/A77", headers=LOCAL).status_code == 404
    book()
    assert app_client.delete("/api/bookings/A10", headers=LOCAL).status_code == 200
    r = app_client.delete("/api/bookings/A10", headers=LOCAL)
    assert r.status_code == 409 and r.json()["code"] == "ALREADY_CANCELLED"


def test_other_origins_without_a_token_are_refused(app_client):
    r = app_client.get("/api/bookings", headers={**LOCAL, "x-forwarded-for": "203.0.113.9"})
    assert r.status_code == 401


def test_a_booking_the_agent_made_is_linked_to_its_call(monkeypatch):
    from core.runtime import runtime as backend
    book()
    call = SimpleNamespace(name="book_appointment", ok=True, value={"booking_id": "A10"})
    other = SimpleNamespace(name="list_free_slots", ok=True, value={"free": []})
    run = SimpleNamespace(tool_objs=[other, call], conversation_id="conv-9", agent_id="agent-9")
    backend.link_bookings(run, "rcpt-9")
    b = next(x for x in ap.all_bookings() if x["booking_id"] == "A10")
    assert (b["conversation_id"], b["receipt_id"], b["agent_id"]) == ("conv-9", "rcpt-9", "agent-9")
    failed = SimpleNamespace(tool_objs=[SimpleNamespace(name="book_appointment", ok=False, value="error")],
                             conversation_id="c", agent_id="a")
    backend.link_bookings(failed, "r")  # nothing to link and no error
