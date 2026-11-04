"""Who a booking is for: the fields of GET /api/bookings and of the /slots bookings, for the owner's screens only."""
import hashlib
import json
from datetime import date, datetime

import pytest

from services import caller_ref as cr, schedule_bookings as sb, schedules as sch
from tests.test_offline_agents import LOCAL

NOW = datetime(2026, 11, 3, 9, 0)
THURSDAY = "2026-11-05"
OWNER = "local-user"


@pytest.fixture()
def desk(monkeypatch, app_client):
    monkeypatch.setattr(sch, "now", lambda tz=None: NOW)
    week = {d: [{"start": "10:00", "end": "13:00"}] for d in ("mon", "tue", "wed", "thu", "fri", "sat")} | {"sun": []}
    s = sch.create({"name": "Doctor consultations", "slot_minutes": 30, "capacity": 3, "week": week, "agent_id": "agent-1"})
    return app_client, s


def make(s, at, name, phone, **kw):
    return sb.create(s, day=date.fromisoformat(THURSDAY), at=at, name=name, phone=phone, **kw)


def test_caller_ref_is_the_documented_hash():
    expected = hashlib.sha256(b"local-user|+919876543210").hexdigest()[:16]
    for number in ("9876543210", "98765 43210", "+91 98765-43210", "919876543210"):
        assert cr.caller_ref(OWNER, number) == expected
    assert cr.caller_ref("someone-else", "9876543210") != expected and cr.caller_ref(OWNER, "") is None
    assert "9876543210" not in expected


def test_the_booking_rows_say_who_they_are_for(desk):
    client, s = desk
    make(s, "10:00", "Sunita Kulkarni", "9876543210", caller=True, source="agent")
    make(s, "10:30", "Sunita Kulkarni", "98765 43210", caller=True, source="agent")
    make(s, "11:00", "Ravi Menon", "9000000003", source="staff")
    rows = {b["id"]: b for b in client.get("/api/bookings", headers=LOCAL).json()["bookings"]}
    a, b, c = rows["A10"], rows["A11"], rows["A12"]
    assert a["name"] == "Sunita Kulkarni" and a["phone_masked"] == "••••3210" and a["phone"] == "••••3210"
    assert a["source"] == "phone" and c["source"] == "staff"
    assert a["caller_ref"] == b["caller_ref"] == cr.caller_ref(OWNER, "9876543210") != c["caller_ref"]
    assert a["caller_bookings_count"] == 2 and c["caller_bookings_count"] == 1
    assert "9876543210" not in json.dumps(rows) and "9000000003" not in json.dumps(rows)


def test_the_source_follows_the_conversation(desk):
    from core import state
    client, s = desk
    from tests.test_offline_agents import make_agent
    conv_ids = {}
    for kind in ("web", "missed_call", "phone"):
        conv = state.local_db.insert("conversations", {"agent_id": "agent-1", "user_id": OWNER, "source": kind, "reply_language": "hi"})
        conv_ids[kind] = conv["id"]
    for i, kind in enumerate(("web", "missed_call", "phone")):
        row = make(s, f"1{i}:00", f"Caller {'ab'[i % 2]}n", f"90000000{i:02d}", caller=True, source="agent")
        sb._ap().annotate(row["booking_id"], conversation_id=conv_ids[kind])
    rows = client.get("/api/bookings", headers=LOCAL).json()["bookings"]
    assert [r["source"] for r in rows] == ["web", "missed_call", "phone"]
    assert all(r["language"] == "hi" and r["conversation_id"] for r in rows)


def test_a_slot_lists_every_booking_with_who_they_are(desk):
    client, s = desk
    for at, name, phone in (("10:00", "Sunita Kulkarni", "9876543210"), ("10:00", "Ravi Menon", "9000000003"),
                            ("10:00", "Arjun Shah", "9000000004")):
        make(s, at, name, phone, source="staff")
    day = client.get(f"/api/schedules/{s['id']}/slots?from={THURSDAY}&to={THURSDAY}", headers=LOCAL).json()["days"][0]
    slot = next(x for x in day["slots"] if x["start"].startswith(f"{THURSDAY}T10:00"))
    assert slot["status"] == "full" and [b["name"] for b in slot["bookings"]] == ["Sunita Kulkarni", "Ravi Menon", "Arjun Shah"]
    first = slot["bookings"][0]
    assert first["phone_masked"] == "••••3210" and first["source"] == "staff" and first["caller_ref"] == cr.caller_ref(OWNER, "9876543210")
    assert first["caller_bookings_count"] == 1 and first["has_evidence"] is False
    assert "9876543210" not in json.dumps(day) and "phone\"" not in json.dumps(day).replace("phone_masked", "")


def test_a_stay_says_how_many_nights(monkeypatch, app_client):
    monkeypatch.setattr(sch, "now", lambda tz=None: NOW)
    week = {d: [{"start": "00:00", "end": "24:00"}] for d in sch.DAYS}
    s = sch.create({"name": "Rooms", "kind": "nights", "capacity": 3, "check_in": "14:00", "check_out": "11:00", "week": week,
                    "agent_id": "agent-1"})
    sb.create(s, day=date.fromisoformat(THURSDAY), at=None, name="Arjun Shah", phone="9000000004", nights=2, source="staff")
    days = app_client.get(f"/api/schedules/{s['id']}/slots?from={THURSDAY}&to={THURSDAY}", headers=LOCAL).json()["days"]
    [b] = days[0]["slots"][0]["bookings"]
    assert b["name"] == "Arjun Shah" and b["nights"] == 2
