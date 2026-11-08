"""Booking safety enforced in the backend: read-back expiry, the schedule rules (minimum notice, cancel notice, bookings per
phone number, capacity under concurrency) and the append-only audit log with GET /api/bookings/audit."""
import json
import threading
from datetime import date, datetime, timedelta, timezone

import pytest

from mcp_servers import appointments as ap
from services import booking_audit, readback, schedule_bookings as sb, schedule_tools as st, schedules as sch, tools

LOCAL = {"storage-mode": "local"}
NOW = datetime(2026, 11, 3, 9, 0)    # Tuesday
THURSDAY = "2026-11-05"
AGENT = "agent-1"


def week():
    return {d: [{"start": "10:00", "end": "13:00"}, {"start": "15:00", "end": "20:00"}]
            for d in ("mon", "tue", "wed", "thu", "fri", "sat")} | {"sun": []}


@pytest.fixture()
def clinic(monkeypatch):
    monkeypatch.setattr(sch, "now", lambda tz=None: NOW)
    return sch.create({"name": "Doctor consultations", "slot_minutes": 60, "week": week(), "agent_id": AGENT})


def make(clinic, at="15:00", day=THURSDAY, name="Priya Sharma", phone="9876543210", **kw):
    kw.setdefault("caller", True)
    kw.setdefault("source", "agent")
    return sb.create(clinic, day=date.fromisoformat(day), at=at, name=name, phone=phone, now=NOW, **kw)


def audit():
    return list(reversed(booking_audit.read(1000)))


# ---------------------------------------------------------------------------------------------- 1. read-back expiry

ARGS = {"name": "Priya Sharma", "phone": "9876543210", "date": "2026-11-09", "time": "11:00"}   # a Monday
READ_BACK = "Monday 9 November at 11 am for Priya Sharma, phone ending 3210. Shall I book it?"
BOOK = tools.ToolSpec("appointments", "book_appointment", "Book.", {"type": "object", "properties": {}})
CANCEL = tools.ToolSpec("appointments", "cancel", "Cancel.", {"type": "object", "properties": {}})


def history(minutes_ago, caller_turns_after=0):
    said = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    rows = [{"role": "user", "text_content": "book me monday 11", "created_at": said.isoformat()},
            {"role": "agent", "text_content": READ_BACK, "created_at": said.isoformat()}]
    rows += [{"role": "user", "text_content": "hmm", "created_at": said.isoformat()}] * caller_turns_after
    return rows


def gate(minutes_ago, turns=0, caller_text="yes please", tool=BOOK, args=ARGS):
    slots = tools.known_slots(history(minutes_ago, turns))
    return tools.readback_error(tool, args, READ_BACK, caller_text, slots)


def test_a_fresh_read_back_still_counts(monkeypatch):
    monkeypatch.setenv("TOOLS_READBACK", "on")
    assert gate(2.5) is None


def test_a_read_back_older_than_three_minutes_no_longer_counts(monkeypatch):
    monkeypatch.setenv("TOOLS_READBACK", "on")
    err = gate(3.5)
    assert err and err.startswith("NOT DONE") and "Shall I book it?" in err


def test_a_read_back_six_caller_turns_ago_no_longer_counts(monkeypatch):
    monkeypatch.setenv("TOOLS_READBACK", "on")
    assert gate(0.5, 5) is None
    assert gate(0.5, 6)


def test_expiry_helper_and_unknown_times():
    now = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
    assert not readback.pending_expired("2026-11-03T08:57:30+00:00", 0, now)
    assert readback.pending_expired("2026-11-03T08:56:00+00:00", 0, now)
    assert readback.pending_expired("2026-11-03T08:59:00", 6, now) and not readback.pending_expired(None, None, now)
    assert not readback.pending_expired("not a time", 2, now)


def test_an_expired_read_back_also_stops_a_cancel(monkeypatch, clinic):
    monkeypatch.setenv("TOOLS_READBACK", "on")
    row = make(clinic, at="15:00")
    spoken = readback.cancel_sentences(row["booking_id"], date.fromisoformat(THURSDAY), datetime.strptime("15:00", "%H:%M").time())["en"]
    spec = tools.ToolSpec("schedules", "cancel", "Cancel.", {"type": "object", "properties": {}})
    spec.agent_id = AGENT
    said = datetime.now(timezone.utc) - timedelta(minutes=5)
    slots = tools.known_slots([{"role": "agent", "text_content": spoken, "created_at": said.isoformat()}])
    err = tools.readback_error(spec, {"booking_id": row["booking_id"]}, spoken, "yes", slots)
    assert err and err.startswith("NOT DONE")
    fresh = tools.known_slots([{"role": "agent", "text_content": spoken, "created_at": datetime.now(timezone.utc).isoformat()}])
    assert tools.readback_error(spec, {"booking_id": row["booking_id"]}, spoken, "yes", fresh) is None


# ---------------------------------------------------------------------------------------------- 2. the rules

def test_minimum_notice_holds_for_a_move_by_the_agent_but_not_for_staff(clinic):
    sch.update(clinic["id"], {"min_notice_minutes": 180})
    clinic = sch.get(clinic["id"])
    make(clinic, at="15:00", day="2026-11-04")
    soon = date(2026, 11, 3)   # today, 10:00 is 60 minutes from now
    with pytest.raises(sb.BookingRefused) as e:
        sb.reschedule("A10", AGENT, day=soon, at="10:00", caller=True, now=NOW)
    assert e.value.result["reason"] == "too_soon"
    new, _ = sb.reschedule("A10", None, day=soon, at="10:00", caller=False, now=NOW)   # staff
    assert new["time"] == "10:00"


def test_cancel_inside_the_notice_window_is_refused_politely(monkeypatch, clinic):
    monkeypatch.setattr(sch, "now", lambda tz=None: datetime(2026, 11, 5, 14, 30))
    sch.update(clinic["id"], {"min_notice_minutes": 0})
    row = sb.create(sch.get(clinic["id"]), day=date(2026, 11, 5), at="15:00", name="Priya Sharma", phone="9876543210",
                    caller=True, source="agent", now=datetime(2026, 11, 5, 14, 30))
    ok, out = st.run("cancel", {"booking_id": row["booking_id"]}, AGENT)
    assert ok is False and out["error"] == "CANCEL_TOO_LATE" and out["suggestion"] == "call_staff"
    assert "front desk" in out["say"]["en"] and "1 hour" in out["say"]["en"] and out["say"]["hi"]
    assert ap.all_bookings()[0]["status"] == "booked"
    with pytest.raises(sb.BookingProblem) as e:
        sb.reschedule(row["booking_id"], AGENT, day=date(2026, 11, 5), at="16:00", caller=True)
    assert e.value.code == "CANCEL_TOO_LATE"
    assert sb.cancel(row["booking_id"], None, caller=False)["status"] == "cancelled"        # staff may
    assert audit()[-1]["event"] == "cancel" and audit()[-1]["actor"] == "staff"


def test_cancel_outside_the_window_works_and_the_window_is_configurable(monkeypatch, clinic):
    row = make(clinic)                                                                    # Thursday 15:00, two days away
    assert sb.cancel(row["booking_id"], AGENT, caller=True)["status"] == "cancelled"
    sch.update(clinic["id"], {"cancel_notice_minutes": 4000})                            # nearly three days
    row = make(sch.get(clinic["id"]), at="16:00")
    with pytest.raises(sb.BookingProblem) as e:
        sb.cancel(row["booking_id"], AGENT, caller=True)
    assert e.value.code == "CANCEL_TOO_LATE" and "2 days" not in e.value.say["en"]


def test_defaults_are_in_the_schedule(clinic):
    assert clinic["cancel_notice_minutes"] == 60 and clinic["max_active_per_phone"] == 3 and clinic["min_notice_minutes"] == 60


def test_the_fourth_booking_for_one_number_is_refused(clinic):
    for hour in ("15:00", "16:00", "17:00"):
        make(clinic, at=hour)
    ok, out = st.run("book", {"schedule": clinic["name"], "date": THURSDAY, "time": "18:00", "name": "Priya Sharma",
                              "phone": "9876543210"}, AGENT)
    assert ok is False and out["error"] == "TOO_MANY_BOOKINGS" and "front desk" in out["say"]["en"]
    assert len([b for b in ap.all_bookings() if b["status"] == "booked"]) == 3
    make(clinic, at="18:00", phone="9000000001", name="Asha Rao")                        # another number is fine
    make(clinic, at="19:00", caller=False, source="staff")                                 # so is staff for the same number
    row = make(clinic, at="10:00", day="2026-11-06", caller=False, source="staff")
    sb.cancel(row["booking_id"], None, caller=False)


def test_a_cancelled_booking_frees_a_place_and_the_limit_is_configurable(clinic):
    rows = [make(clinic, at=h) for h in ("15:00", "16:00", "17:00")]
    sb.cancel(rows[0]["booking_id"], AGENT, caller=True)
    make(clinic, at="18:00")
    sch.update(clinic["id"], {"max_active_per_phone": 0})                                 # no limit
    make(sch.get(clinic["id"]), at="19:00")
    sch.update(clinic["id"], {"max_active_per_phone": 1})
    with pytest.raises(sb.BookingProblem) as e:
        make(sch.get(clinic["id"]), at="10:00", day="2026-11-06")
    assert e.value.code == "TOO_MANY_BOOKINGS"


def test_capacity_holds_when_threads_race_for_the_last_place(clinic):
    sch.update(clinic["id"], {"capacity": 2, "max_active_per_phone": 0})
    schedule = sch.get(clinic["id"])
    results = []

    def go(i):
        try:
            sb.create(schedule, day=date.fromisoformat(THURSDAY), at="15:00", name=f"Person {chr(65 + i)}{chr(66 + i)}",
                      phone=f"98765432{i:02d}", caller=True, source="agent", now=NOW)
            results.append("ok")
        except sb.BookingRefused as e:
            results.append(e.result["reason"])

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 2 and results.count("full") == 6
    assert len([b for b in ap.all_bookings() if b["status"] == "booked"]) == 2


# ---------------------------------------------------------------------------------------------- 3. the audit log

def test_create_move_cancel_and_refusals_are_logged_with_masked_phone(monkeypatch, clinic):
    token = booking_audit.bind_call("conv-1")
    try:
        row = make(clinic)
        sb.reschedule(row["booking_id"], AGENT, day=date.fromisoformat(THURSDAY), at="16:00", caller=True, now=NOW)
        with pytest.raises(sb.BookingRefused):
            make(clinic, at="16:00", name="Asha Rao", phone="9000000003", conversation=None) if False else sb.create(
                clinic, day=date.fromisoformat(THURSDAY), at="16:00", name="Asha Rao", phone="9000000003", caller=True,
                source="agent", now=NOW)
        sb.cancel(row["booking_id"], AGENT, caller=True)
    finally:
        booking_audit.reset_call(token)
    log = audit()
    assert [e["event"] for e in log] == ["create", "move", "refusal", "cancel"]
    create_e, move_e, refusal_e, cancel_e = log
    assert create_e["actor"] == "agent" and create_e["conversation_id"] == "conv-1" and create_e["booking_id"] == "A10"
    assert create_e["phone"] == "••••3210" and create_e["before"] is None and create_e["after"]["time"] == "15:00"
    assert move_e["before"]["time"] == "15:00" and move_e["after"]["time"] == "16:00"
    assert refusal_e["reason"] == "FULL" and refusal_e["phone"] == "••••0003" and refusal_e["attempted"]["time"] == "16:00"
    assert cancel_e["before"]["status"] == "booked" and cancel_e["after"]["status"] == "cancelled"
    assert all(e["ts"] for e in log) and "9876543210" not in json.dumps(log)


def test_the_log_is_append_only_jsonl(clinic):
    make(clinic)
    path = booking_audit.audit_path()
    first = open(path).read()
    make(clinic, at="16:00")
    second = open(path).read()
    assert second.startswith(first) and len(second.splitlines()) == 2
    assert all(json.loads(line)["event"] == "create" for line in second.splitlines())


def test_a_rule_refusal_is_logged_with_its_code(clinic):
    for hour in ("15:00", "16:00", "17:00"):
        make(clinic, at=hour)
    with pytest.raises(sb.BookingProblem):
        make(clinic, at="18:00")
    assert audit()[-1]["event"] == "refusal" and audit()[-1]["reason"] == "TOO_MANY_BOOKINGS"


def test_audit_endpoint_lists_newest_first_with_a_limit_and_staff_actions(app_client, monkeypatch, clinic):
    monkeypatch.setattr(sch, "now", lambda tz=None: NOW)
    r = app_client.post("/api/bookings", headers=LOCAL, json={"schedule_id": clinic["id"], "start": f"{THURSDAY}T15:00",
                                                              "name": "Priya Sharma", "phone": "9876543210"})
    assert r.status_code == 200, r.text
    booking_id = r.json()["booking"]["id"]
    assert app_client.patch(f"/api/bookings/{booking_id}", headers=LOCAL, json={"start": f"{THURSDAY}T16:00"}).status_code == 200
    assert app_client.delete(f"/api/bookings/{booking_id}", headers=LOCAL).status_code == 200
    assert app_client.delete(f"/api/bookings/{booking_id}", headers=LOCAL).status_code == 409
    out = app_client.get("/api/bookings/audit", headers=LOCAL).json()
    assert [e["event"] for e in out["entries"]] == ["refusal", "cancel", "move", "create"] and out["count"] == 4
    assert all(e["actor"] == "staff" for e in out["entries"]) and out["entries"][0]["reason"] == "ALREADY_CANCELLED"
    assert "owner" not in out["entries"][0] and out["entries"][-1]["phone"] == "••••3210"
    assert len(app_client.get("/api/bookings/audit?limit=2", headers=LOCAL).json()["entries"]) == 2
    assert app_client.get("/api/bookings/audit?limit=0", headers=LOCAL).status_code == 422


def test_audit_endpoint_hides_other_owners_entries(app_client):
    booking_audit.record("create", actor="staff", booking_id="A1", owner="someone-else")
    booking_audit.record("create", actor="agent", booking_id="A2")
    ids = [e["booking_id"] for e in app_client.get("/api/bookings/audit", headers=LOCAL).json()["entries"]]
    assert ids == ["A2"]
