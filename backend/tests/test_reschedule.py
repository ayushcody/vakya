"""Moving a booking (services/schedule_bookings.reschedule, the `reschedule` tool, PATCH /api/bookings/{id}), the read-back
gates of reschedule and cancel, the calendar event that follows the booking, and the hand-over to the WhatsApp module.
Google (services/gcal.py) is faked; nothing talks to it."""
import threading
from datetime import date, datetime

import pytest

from mcp_servers import appointments as ap
from services import booking_evidence, gcal, readback, schedule_bookings as sb, schedule_tools as st, schedules as sch, tools

LOCAL = {"storage-mode": "local"}
NOW = datetime(2026, 11, 3, 9, 0)    # Tuesday
THURSDAY, FRIDAY = "2026-11-05", "2026-11-06"
AGENT = "agent-1"


def clinic_week():
    return {d: [{"start": "10:00", "end": "13:00"}, {"start": "15:00", "end": "20:00"}]
            for d in ("mon", "tue", "wed", "thu", "fri", "sat")} | {"sun": []}


@pytest.fixture()
def clinic(monkeypatch):
    monkeypatch.setattr(sch, "now", lambda tz=None: NOW)
    return sch.create({"name": "Doctor consultations", "slot_minutes": 60, "week": clinic_week(), "agent_id": AGENT})


def make(clinic, at="15:00", day=THURSDAY, name="Priya Sharma", phone="9876543210", **kw):
    return sb.create(clinic, day=date.fromisoformat(day), at=at, name=name, phone=phone, caller=True, source="agent", now=NOW, **kw)


def stored(booking_id="A10"):
    return next(b for b in ap.all_bookings() if b["booking_id"] == booking_id)


def move(booking_id="A10", at="16:00", day=THURSDAY, **kw):
    kw.setdefault("now", NOW)
    return sb.reschedule(booking_id, AGENT, day=date.fromisoformat(day), at=at, caller=True, **kw)


# ---------------------------------------------------------------------------------------------- the move itself

def test_a_booking_moves_in_place_and_keeps_its_id(clinic):
    row = make(clinic)
    assert row["version"] == 1
    new, previous = move()
    assert new["booking_id"] == "A10" == previous["booking_id"] and previous["time"] == "15:00" and previous["version"] == 1
    assert (new["date"], new["time"], new["weekday"]) == (THURSDAY, "16:00", "Thursday")
    assert new["start"] == "2026-11-05T16:00" and new["end"] == "2026-11-05T17:00"
    assert new["version"] == 2 and new["moved_from"] == {"date": THURSDAY, "time": "15:00", "start": "2026-11-05T15:00"}
    assert new["moved_at"] and len([b for b in ap.all_bookings() if b["status"] == "booked"]) == 1


def test_it_can_move_to_a_time_that_overlaps_itself(clinic):
    make(clinic)
    sch.update(clinic["id"], {"slot_minutes": 30})
    new, _ = move(at="15:30")
    assert new["time"] == "15:30"


def test_a_taken_time_refuses_with_alternatives_and_leaves_the_booking_alone(clinic):
    make(clinic)
    make(clinic, at="16:00", name="Asha Rao", phone="9000000003")
    before = stored("A10")
    with pytest.raises(sb.BookingRefused) as e:
        move("A10", at="16:00")
    assert e.value.result["reason"] == "full" and e.value.result["before"] and e.value.result["after"]
    assert stored("A10") == before


def test_each_move_and_a_cancel_raise_the_version(clinic):
    make(clinic)
    move(at="16:00")
    new, previous = move(at="17:00")
    assert new["version"] == 3 and previous["time"] == "16:00" and new["moved_from"]["time"] == "16:00"
    assert sb.cancel("A10", AGENT, caller=True)["version"] == 4


def test_the_rules_of_cancel_apply(clinic):
    make(clinic)
    with pytest.raises(sb.BookingProblem) as e:
        sb.reschedule("A99", AGENT, day=date.fromisoformat(THURSDAY), at="16:00", caller=True, now=NOW)
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(sb.BookingProblem) as e:
        sb.reschedule("A10", "another-desk", day=date.fromisoformat(THURSDAY), at="16:00", caller=True, now=NOW)
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(sb.BookingProblem) as e:
        move(at="15:00")
    assert e.value.code == "SAME_TIME"
    sb.cancel("A10", AGENT, caller=True)
    with pytest.raises(sb.BookingProblem) as e:
        move()
    assert e.value.code == "ALREADY_CANCELLED"


def test_a_booking_in_the_past_cannot_be_moved_by_a_caller(clinic, monkeypatch):
    make(clinic)
    later = datetime(2026, 11, 5, 18, 0)
    monkeypatch.setattr(sch, "now", lambda tz=None: later)
    with pytest.raises(sb.BookingProblem) as e:
        sb.reschedule("A10", AGENT, day=date(2026, 11, 6), at="16:00", caller=True, now=later)
    assert e.value.code == "IN_THE_PAST"


def test_a_caller_is_held_to_the_minimum_notice_but_staff_are_not(clinic):
    make(clinic)
    soon = datetime(2026, 11, 5, 14, 30)       # 15:00 is 30 minutes away: the default notice is 60
    sch.update(clinic["id"], {"min_notice_minutes": 60})
    with pytest.raises(sb.BookingRefused) as e:
        sb.reschedule("A10", AGENT, day=date(2026, 11, 5), at="15:00", caller=True, now=soon)   # the same hour is too soon
    assert e.value.result["reason"] == "too_soon"


def test_a_stay_keeps_its_nights_unless_told_otherwise(monkeypatch):
    monkeypatch.setattr(sch, "now", lambda tz=None: NOW)
    rooms = sch.create({"name": "Rooms", "kind": "nights", "capacity": 2, "check_in": "14:00", "check_out": "11:00",
                        "week": {d: [{"start": "00:00", "end": "24:00"}] for d in sch.DAYS}, "agent_id": AGENT})
    sb.create(rooms, day=date(2026, 11, 10), at=None, name="Meera Joshi", phone="9876543210", nights=2, caller=True,
              source="agent", now=NOW)
    new, previous = sb.reschedule("A10", AGENT, day=date(2026, 11, 12), at=None, caller=True, now=NOW)
    assert new["nights"] == 2 and new["date"] == "2026-11-12" and new["end"] == "2026-11-14T11:00" and previous["date"] == "2026-11-10"


# ---------------------------------------------------------------------------------------------- the calendar event follows

class Fake:
    def __init__(self):
        self.created, self.moved, self.deleted = [], [], []
        self.fail_move = None
        self.gate = None

    def create_event(self, summary, description, start, minutes, location="", attendees=None, request_id=""):
        self.created.append({"description": description, "start": start, "minutes": minutes})
        return f"evt{len(self.created)}"

    def move_event(self, event_id, start, minutes, description=None):
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail_move:
            raise self.fail_move
        self.moved.append({"id": event_id, "start": start, "minutes": minutes, "description": description})

    def delete_event(self, event_id):
        self.deleted.append(event_id)


@pytest.fixture()
def google(monkeypatch):
    fake = Fake()
    monkeypatch.setattr(gcal, "configured", lambda: True)
    monkeypatch.setattr(gcal, "day_busy", lambda d, tz=None: [])
    for name in ("create_event", "move_event", "delete_event"):
        monkeypatch.setattr(gcal, name, getattr(fake, name))
    return fake


def test_the_event_is_patched_with_the_new_time_and_a_moved_from_line(clinic, google):
    make(clinic)
    new, _ = move()
    assert google.moved == [{"id": "evt1", "start": datetime(2026, 11, 5, 16, 0), "minutes": 60,
                             "description": google.moved[0]["description"]}]
    assert "Moved from Thu 5 Nov 3:00 pm" in google.moved[0]["description"] and "A10" in google.moved[0]["description"]
    assert new["calendar_sync"] == "synced" and new["calendar_event_id"] == "evt1" and len(google.created) == 1


def test_a_booking_with_no_event_gets_one_when_it_moves(clinic, monkeypatch, google):
    monkeypatch.setattr(gcal, "configured", lambda: False)
    make(clinic)                                   # made while the calendar was off: no event
    monkeypatch.setattr(gcal, "configured", lambda: True)
    new, _ = move()
    assert google.moved == [] and google.created[0]["start"] == datetime(2026, 11, 5, 16, 0) and new["calendar_event_id"] == "evt1"


def test_an_event_the_owner_deleted_is_made_again(clinic, google):
    make(clinic)
    google.fail_move = gcal.CalendarError("gone", 404)
    new, _ = move()
    assert len(google.created) == 2 and new["calendar_event_id"] == "evt2" and new["calendar_sync"] == "synced"


def test_a_failing_calendar_never_undoes_the_move(clinic, google):
    make(clinic)
    google.fail_move = gcal.CalendarError("Google Calendar said 500")
    new, _ = move()
    assert new["time"] == "16:00" and new["calendar_sync"] == "failed" and "500" in new["calendar_error"]


def test_a_slow_google_does_not_hold_up_the_move(clinic, google, monkeypatch):
    make(clinic)
    monkeypatch.setattr(ap, "CALENDAR_WAIT_S", 0.1)
    google.gate = threading.Event()
    new, _ = move()
    assert new["time"] == "16:00" and new["calendar_sync"] == "pending"
    google.gate.set()


def test_nothing_is_sent_without_a_calendar(clinic, monkeypatch):
    monkeypatch.setattr(gcal, "move_event", lambda *a, **k: pytest.fail("no calendar is set up"))
    make(clinic)
    new, _ = move()
    assert "calendar_sync" not in new


# ---------------------------------------------------------------------------------------------- the tool

def run(tool, **args):
    return st.run(tool, args, AGENT)


def test_the_reschedule_tool_moves_and_says_so(clinic):
    run("book", date=THURSDAY, time="15:00", name="Priya Sharma", phone="9876543210")
    ok, v = run("reschedule", booking_id="A10", date=THURSDAY, time="16:00")
    assert ok and v["booking_id"] == "A10" and v["time"] == "16:00" and v["moved_from"] == {"date": THURSDAY, "weekday": "Thursday", "time": "15:00"}
    assert v["say"]["en"] == "Moved. Booking A10 is now Thursday at 4 pm." and "A10" in v["say"]["hi"] and "phone" not in v


def test_the_tool_offers_alternatives_when_the_new_time_is_taken(clinic):
    run("book", date=THURSDAY, time="15:00", name="Priya Sharma", phone="9876543210")
    run("book", date=THURSDAY, time="16:00", name="Asha Rao", phone="9000000003")
    ok, v = run("reschedule", booking_id="A10", date=THURSDAY, time="16:00")
    assert not ok and v["moved"] is False and v["reason"] == "full" and v["alternatives"]
    assert stored("A10")["time"] == "15:00"


def test_the_tool_answers_in_words_for_problems(clinic):
    assert run("reschedule", booking_id="A99", date=THURSDAY, time="16:00") == (False, "There is no booking A99.")
    assert run("reschedule", booking_id="A10", time="16:00")[0] is False


def test_the_tool_description_tells_the_model_to_find_the_booking_first():
    d = next(t for t in st.TOOLS if t["tool"] == "reschedule")["description"]
    assert "find_booking" in d and "more than one" in d and "instead of cancel and book" in d


# ---------------------------------------------------------------------------------------------- the read-back gates

def spec(name):
    d = next(t for t in st.TOOLS if t["tool"] == name)
    s = tools.ToolSpec(st.SERVER, name, d["description"], d["schema"])
    s.agent_id = AGENT
    s.runner = lambda tool, args: st.run(tool, args, AGENT)
    return s


@pytest.fixture()
def gate(monkeypatch):
    monkeypatch.setenv("TOOLS_READBACK", "on")


MOVE_ARGS = {"booking_id": "A10", "date": THURSDAY, "time": "16:00"}


def test_reschedule_needs_the_new_time_read_back_and_a_yes(clinic, gate):
    make(clinic)
    first = tools.execute(spec("reschedule"), MOVE_ARGS, previous_agent_text="Sure.", caller_text="move it to 4")
    assert not first.ok and first.value.startswith("NOT DONE") and stored("A10")["time"] == "15:00"
    for lang in ("EN", "HI", "MR"):
        assert f"{lang} \"" in first.value
    assert "Moving your booking to Thursday 5 November at 4 pm. Shall I go ahead?" in first.value
    read_back = "Moving your booking to Thursday 5 November at 4 pm. Shall I go ahead?"
    no = tools.execute(spec("reschedule"), MOVE_ARGS, previous_agent_text=read_back, caller_text="no, make it 5")
    assert not no.ok and stored("A10")["time"] == "15:00"
    yes = tools.execute(spec("reschedule"), MOVE_ARGS, previous_agent_text=read_back, caller_text="yes please")
    assert yes.ok and stored("A10")["time"] == "16:00"


def test_an_offer_of_two_times_is_not_a_read_back_for_a_move(clinic, gate):
    make(clinic)
    c = tools.execute(spec("reschedule"), MOVE_ARGS, previous_agent_text="4 pm or 5 pm, which would suit you?",
                      caller_text="4")
    assert not c.ok and stored("A10")["time"] == "15:00"


@pytest.mark.parametrize("ask", [
    "Shall I cancel booking A10, Thursday 5 November at 3 pm?",
    "क्या मैं बुकिंग A10, गुरुवार 5 नवंबर दोपहर 3 बजे, रद्द कर दूँ?",
    "मी बुकिंग A10, गुरुवार 5 नोव्हेंबर दुपारी 3 वाजता, रद्द करू का?"])
def test_cancel_runs_after_its_day_and_time_were_read_back_and_a_yes(clinic, gate, ask):
    make(clinic)
    c = tools.execute(spec("cancel"), {"booking_id": "A10"}, previous_agent_text=ask, caller_text="yes")
    assert c.ok and stored("A10")["status"] == "cancelled"


def test_cancel_without_the_day_and_time_is_refused_with_the_sentence_in_three_languages(clinic, gate):
    make(clinic)
    c = tools.execute(spec("cancel"), {"booking_id": "A10"}, previous_agent_text="Shall I cancel booking A10?", caller_text="yes")
    assert not c.ok and c.value.startswith("NOT DONE") and stored("A10")["status"] == "booked"
    assert "Shall I cancel booking A10, Thursday 5 November at 3 pm?" in c.value
    assert "क्या मैं बुकिंग A10, गुरुवार 5 नवंबर दोपहर 3 बजे, रद्द कर दूँ?" in c.value
    assert "मी बुकिंग A10, गुरुवार 5 नोव्हेंबर दुपारी 3 वाजता, रद्द करू का?" in c.value


def test_cancel_needs_a_plain_yes(clinic, gate):
    make(clinic)
    ask = "Shall I cancel booking A10, Thursday 5 November at 3 pm?"
    for said in ("no wait", "what about Friday", "hmm"):
        c = tools.execute(spec("cancel"), {"booking_id": "A10"}, previous_agent_text=ask, caller_text=said)
        assert not c.ok, said
    assert stored("A10")["status"] == "booked"


def test_cancel_of_another_desks_booking_is_not_described(clinic, gate):
    make(clinic)
    other = spec("cancel")
    other.agent_id = "another-desk"
    other.runner = lambda tool, args: st.run(tool, args, "another-desk")
    c = tools.execute(other, {"booking_id": "A10"}, previous_agent_text="x", caller_text="yes")
    assert not c.ok and "A10" in c.value and "Thursday" not in c.value        # the tool itself says: there is no such booking


def test_the_sentences_pass_the_read_back_check_in_every_language():
    d, t = date(2026, 11, 5), datetime(2026, 11, 5, 16, 0).time()
    for lang, text in readback.sentences({"date": THURSDAY, "time": "16:00"}, "move").items():
        assert readback.slot_readback_ok(text, d, t), (lang, text)
    for lang, text in readback.cancel_sentences("A10", d, t).items():
        assert readback.slot_readback_ok(text, d, t), (lang, text)


# ---------------------------------------------------------------------------------------------- the desk

def test_the_desk_moves_a_booking(app_client, clinic):
    make(clinic)
    r = app_client.patch("/api/bookings/A10", json={"start": f"{THURSDAY}T16:00"}, headers=LOCAL)
    assert r.status_code == 200, r.text
    b = r.json()["booking"]
    assert b["id"] == "A10" and b["time"] == "16:00" and b["version"] == 2 and b["moved_from"]["time"] == "15:00" and b["moved_at"]
    assert b["start"].startswith("2026-11-05T16:00")


def test_the_desk_is_not_held_to_the_minimum_notice(app_client, clinic):
    make(clinic)
    sch.update(clinic["id"], {"min_notice_minutes": 1440})
    r = app_client.patch("/api/bookings/A10", json={"start": f"{THURSDAY}T17:00"}, headers=LOCAL)
    assert r.status_code == 200, r.text


def test_a_taken_time_is_a_409_with_the_nearest_times(app_client, clinic):
    make(clinic)
    make(clinic, at="16:00", name="Asha Rao", phone="9000000003")
    r = app_client.patch("/api/bookings/A10", json={"start": f"{THURSDAY}T16:00"}, headers=LOCAL)
    assert r.status_code == 409 and r.json()["code"] == "SLOT_UNAVAILABLE"
    assert r.json()["details"]["reason"] == "full" and r.json()["details"]["before"] and r.json()["details"]["after"]
    assert stored("A10")["time"] == "15:00"


def test_the_desk_answers_for_unknown_cancelled_and_unmovable_bookings(app_client, clinic):
    make(clinic)
    assert app_client.patch("/api/bookings/A99", json={"start": f"{THURSDAY}T16:00"}, headers=LOCAL).status_code == 404
    assert app_client.patch("/api/bookings/A10", json={"start": "nonsense"}, headers=LOCAL).status_code == 422
    app_client.delete("/api/bookings/A10", headers=LOCAL)
    r = app_client.patch("/api/bookings/A10", json={"start": f"{THURSDAY}T16:00"}, headers=LOCAL)
    assert r.status_code == 409 and r.json()["code"] == "ALREADY_CANCELLED"
    ap.book_appointment(name="Old Style", phone="9876543210", date="2026-11-09", time="10:30")
    r = app_client.patch("/api/bookings/A11", json={"start": f"{THURSDAY}T16:00"}, headers=LOCAL)
    assert r.status_code == 422 and r.json()["code"] == "NOT_MOVABLE"


def test_a_desk_cancel_raises_the_version(app_client, clinic):
    make(clinic)
    r = app_client.delete("/api/bookings/A10", headers=LOCAL)
    assert r.json()["booking"]["version"] == 2


# ---------------------------------------------------------------------------------------------- the proof

def test_a_move_and_a_cancel_are_written_on_the_proof_without_touching_the_seal(clinic, tmp_path, monkeypatch):
    monkeypatch.setenv("VAKYA_BOOKING_EVIDENCE_DIR", str(tmp_path / "evidence"))
    eid = "e" * 32
    folder = tmp_path / "evidence" / eid
    folder.mkdir(parents=True)
    make(clinic)
    ap.annotate("A10", evidence_id=eid)
    seal = (folder / "hashes.json")
    seal.write_text("{}")
    move()
    sb.cancel("A10", AGENT, caller=True)
    changes = booking_evidence.changes_of(eid)
    assert [c["kind"] for c in changes] == ["moved", "cancelled"]
    assert changes[0]["moved_from"]["time"] == "15:00" and changes[0]["to"]["time"] == "16:00"
    assert seal.read_text() == "{}"


def test_recording_a_change_for_an_unknown_proof_is_harmless(tmp_path, monkeypatch):
    monkeypatch.setenv("VAKYA_BOOKING_EVIDENCE_DIR", str(tmp_path / "evidence"))
    assert booking_evidence.record_change("f" * 32, "moved") is False
    assert booking_evidence.record_change("not an id", "moved") is False


# ---------------------------------------------------------------------------------------------- the WhatsApp hand-over

@pytest.fixture()
def notify(monkeypatch):
    from services import booking_notify
    seen = []

    def changed(kind, booking, previous=None, *, source="agent"):
        seen.append((kind, booking["booking_id"], (previous or {}).get("time"), source, booking.get("phone")))
        return {"status": "ask", "number_ending": "3210"}
    monkeypatch.setattr(booking_notify, "booking_changed", changed)
    monkeypatch.setattr(booking_notify, "record_consent", lambda agreed: {"ok": True, "agreed": agreed})
    return seen


def test_book_move_and_cancel_tell_the_whatsapp_module_and_show_its_answer(clinic, notify):
    ok, booked = run("book", date=THURSDAY, time="15:00", name="Priya Sharma", phone="9876543210")
    assert ok and booked["whatsapp"] == {"status": "ask", "number_ending": "3210"}
    ok, moved = run("reschedule", booking_id="A10", date=THURSDAY, time="16:00")
    assert ok and moved["whatsapp"]["status"] == "ask"
    ok, cancelled = run("cancel", booking_id="A10")
    assert ok and cancelled["whatsapp"]["status"] == "ask"
    assert notify == [("booked", "A10", None, "agent", "9876543210"), ("moved", "A10", "15:00", "agent", "9876543210"),
                      ("cancelled", "A10", None, "agent", "9876543210")]


def test_the_desk_tells_it_with_source_staff(app_client, clinic, notify):
    r = app_client.post("/api/bookings", json={"schedule_id": clinic["id"], "start": f"{THURSDAY}T15:00", "name": "Priya Sharma",
                                               "phone": "9876543210"}, headers=LOCAL)
    assert r.status_code == 200, r.text
    app_client.patch("/api/bookings/A10", json={"start": f"{THURSDAY}T16:00"}, headers=LOCAL)
    app_client.delete("/api/bookings/A10", headers=LOCAL)
    assert [(k, s) for k, _b, _p, s, _ph in notify] == [("booked", "staff"), ("moved", "staff"), ("cancelled", "staff")]


def test_a_module_that_raises_or_is_absent_changes_nothing(clinic, monkeypatch):
    from services import booking_notify
    monkeypatch.setattr(booking_notify, "booking_changed", lambda *a, **k: 1 / 0)
    ok, booked = run("book", date=THURSDAY, time="15:00", name="Priya Sharma", phone="9876543210")
    assert ok and "whatsapp" not in booked
    import builtins
    real = builtins.__import__

    def no_module(name, *a, **k):
        if name.endswith("booking_notify") or (a and len(a) > 2 and a[2] and "booking_notify" in a[2]):
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_module)
    assert st._notify("booked", {"booking_id": "A10"}) is None
    assert st._whatsapp_module() is None
    assert st.run("whatsapp_consent", {"agreed": True}, AGENT) == (False, "WhatsApp is not set up.")


def test_the_consent_tool_passes_the_answer_on(clinic, notify):
    assert run("whatsapp_consent", agreed=True) == (True, {"ok": True, "agreed": True})
    assert run("whatsapp_consent", agreed="yes")[0] is False


def test_the_consent_tool_is_offered_only_when_the_module_exists(clinic, monkeypatch):
    names = [s.tool for s in st.specs_for_agent(AGENT, tools)]
    assert "whatsapp_consent" in names and "reschedule" in names
    monkeypatch.setattr(st, "_whatsapp_module", lambda: None)
    assert "whatsapp_consent" not in [s.tool for s in st.specs_for_agent(AGENT, tools)]


def test_the_prompt_carries_the_guidance(clinic):
    text = tools.tools_prompt(st.specs_for_agent(AGENT, tools), json_protocol=False)
    assert "Never cancel and book again" in text and "whatsapp.status" in text and "whatsapp_consent" in text
