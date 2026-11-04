"""services/schedules.py (validation, store, plain words) and services/availability.py (slots, statuses, nearest free times,
spoken answers). The clock is Tuesday 3 November 2026, 09:00 in Asia/Kolkata, which has no daylight saving."""
import json
import random
from datetime import date, datetime, timedelta

import pytest

from services import availability as av, schedules as sch

NOW = datetime(2026, 11, 3, 9, 0)   # a Tuesday
TODAY = NOW.date()


def week(days, *ranges):
    names = sch.DAYS
    return {n: [{"start": a, "end": b} for a, b in ranges] if i in days else [] for i, n in enumerate(names)}


CLINIC_WEEK = week(range(6), ("10:00", "13:00"), ("17:00", "20:00"))


def make(**kw):
    base = {"name": "Doctor consultations", "slot_minutes": 30, "capacity": 1, "week": CLINIC_WEEK, "id": "S1"}
    base.update(kw)
    row = sch.normalise_schedule(base)
    row["id"] = "S1"
    return row


def booking(start, minutes=30, name="Asha", bid="A10", **kw):
    s = datetime.fromisoformat(start)
    return {"schedule_id": "S1", "booking_id": bid, "name": name, "status": "booked", "start": s.isoformat(),
            "end": (s + timedelta(minutes=minutes)).isoformat(), **kw}


def engine(schedule=None, bookings=(), blocks=(), now=NOW):
    schedule = schedule or make()
    return av.Engine(schedule, av.live_bookings(list(bookings), "S1"), list(blocks), now)


def block(start, end, bid="K1"):
    return {"id": bid, "start": start, "end": end}


def times(eng, d):
    return [s.start.strftime("%H:%M") for s in eng.slots(d)]


# ---------------------------------------------------------------------------------------------- validation

@pytest.mark.parametrize("bad", [
    {"mon": [{"start": "9:00", "end": "8:00"}]},                                   # ends before it starts
    {"mon": [{"start": "09:00", "end": "09:00"}]},                                 # empty
    {"mon": [{"start": "09:00", "end": "12:00"}, {"start": "11:00", "end": "14:00"}]},   # overlap
    {"mon": [{"start": "25:00", "end": "26:00"}]},
    {"mon": [{"start": "09:60", "end": "10:00"}]},
    {"mon": [{"start": "nine", "end": "ten"}]},
    {"mon": [{"start": "09:00"}]},
    {"mon": "closed"},
    {"funday": []},
    {"mon": [{"start": "00:00", "end": "01:00"}] * 7},
])
def test_a_bad_week_is_rejected(bad):
    with pytest.raises(sch.ScheduleError):
        sch.normalise_week(bad)


def test_a_week_is_sorted_padded_and_complete():
    w = sch.normalise_week({"MON": [{"start": "17:00", "end": "20:00"}, {"start": "9:00", "end": "13:00"}],
                            "tue": [{"start": "00:00", "end": "23:59"}]})
    assert list(w) == list(sch.DAYS)
    assert w["mon"] == [{"start": "09:00", "end": "13:00"}, {"start": "17:00", "end": "20:00"}]
    assert w["tue"] == [{"start": "00:00", "end": "24:00"}] and w["wed"] == []
    touching = sch.normalise_week({"mon": [{"start": "09:00", "end": "12:00"}, {"start": "12:00", "end": "14:00"}]})
    assert len(touching["mon"]) == 2


@pytest.mark.parametrize("field,value", [
    ("slot_minutes", 4), ("slot_minutes", 241), ("slot_minutes", 7.5), ("buffer_minutes", -1), ("buffer_minutes", 121),
    ("capacity", 0), ("capacity", 501), ("horizon_days", 0), ("horizon_days", 366), ("min_notice_minutes", -5),
    ("kind", "rooms"), ("accent", "pink"), ("timezone", "Mars/Olympus"), ("name", "   "), ("name", "x" * 81),
    ("closed_dates", ["12 November"]), ("slot_minutes", True), ("agent_id", 5),
])
def test_bad_numbers_and_words_are_rejected(field, value):
    with pytest.raises(sch.ScheduleError):
        sch.normalise_schedule({"name": "A", "week": CLINIC_WEEK, **{field: value}})


def test_defaults_and_a_nightly_schedule():
    s = sch.normalise_schedule({"name": "  Rooms  ", "kind": "nights", "capacity": 10, "check_in": "12:00",
                                "check_out": "11:00", "week": week(range(7), ("00:00", "24:00")),
                                "closed_dates": ["2026-12-25", "2026-12-25", "2026-11-14"]})
    assert s["name"] == "Rooms" and s["accent"] == "violet" and s["timezone"] == "Asia/Kolkata"
    assert (s["min_notice_minutes"], s["horizon_days"], s["buffer_minutes"]) == (60, 30, 0)
    assert s["closed_dates"] == ["2026-11-14", "2026-12-25"]
    for bad in ({"check_in": None}, {"check_out": "13:00"}, {"check_in": "noon"}):
        with pytest.raises(sch.ScheduleError):
            sch.normalise_schedule({"name": "Rooms", "kind": "nights", "check_in": "12:00", "check_out": "11:00",
                                    "week": s["week"], **bad})


def test_the_store_creates_updates_blocks_and_deletes():
    a = sch.create({"name": "One", "week": CLINIC_WEEK})
    b = sch.create({"name": "Two", "week": CLINIC_WEEK, "agent_id": "agent-1"})
    assert (a["id"], b["id"]) == ("S1", "S2")
    assert [s["id"] for s in sch.list_schedules("agent-1")] == ["S2"]
    changed = sch.update("S1", {"slot_minutes": 20})
    assert changed["slot_minutes"] == 20 and changed["name"] == "One" and changed["week"] == a["week"]
    with pytest.raises(sch.ScheduleError):
        sch.update("S1", {"slot_minutes": 1})
    assert sch.get("S1")["slot_minutes"] == 20            # a rejected update changes nothing
    assert sch.update("S9", {"name": "x"}) is None
    k = sch.add_block("S1", {"start": "2026-11-05T10:00:00+05:30", "end": "2026-11-05T10:30:00+05:30", "reason": "lunch"})
    assert k["id"] == "K1" and k["start"] == "2026-11-05T10:00"
    assert sch.add_block("S1", {"start": "2026-11-05T10:00", "end": "2026-11-05T10:30"})["id"] == "K1"   # no duplicate
    assert sch.add_block("S9", {"start": "2026-11-05T10:00", "end": "2026-11-05T10:30"}) is None
    with pytest.raises(sch.ScheduleError):
        sch.add_block("S1", {"start": "2026-11-05T11:00", "end": "2026-11-05T10:30"})
    assert [x["id"] for x in sch.list_blocks("S1")] == ["K1"]
    assert sch.remove_block("S1", "K1") and not sch.remove_block("S1", "K1")
    sch.add_block("S1", {"start": "2026-11-05T10:00", "end": "2026-11-05T10:30"})
    assert sch.delete("S1") and not sch.delete("S1") and sch.list_blocks("S1") == []


def test_a_corrupt_file_is_set_aside(tmp_path, monkeypatch):
    path = tmp_path / "bad" / "schedules.json"
    path.parent.mkdir()
    path.write_text("{not json")
    monkeypatch.setenv("VAKYA_SCHEDULES_FILE", str(path))
    assert sch.list_schedules() == []
    assert path.with_name("schedules.json.corrupt").exists()
    assert sch.create({"name": "Fresh", "week": CLINIC_WEEK})["id"] == "S1"


def test_hours_in_plain_words():
    assert sch.hours_text(make()) == "Mon to Sat, 10:00 to 13:00 and 17:00 to 20:00"
    mixed = make(week={**week([0, 1, 2], ("09:00", "17:00")), "fri": [{"start": "10:00", "end": "12:00"}]})
    assert sch.hours_text(mixed) == "Mon to Wed, 09:00 to 17:00; Fri, 10:00 to 12:00"
    assert sch.hours_text(make(week=week([0, 2], ("10:00", "11:00")))) == "Mon and Wed, 10:00 to 11:00"
    assert sch.hours_text(make(week=week(range(7), ("00:00", "24:00")))) == "Every day, open 24 hours"
    assert sch.hours_text(make(week=week([]))) == "Closed"
    rooms = make(kind="nights", check_in="12:00", check_out="11:00", week=week(range(7), ("00:00", "24:00")))
    assert sch.hours_text(rooms) == "Every day, check-in 12:00, check-out 11:00"


# ---------------------------------------------------------------------------------------------- slots

def test_slots_follow_the_ranges_and_breaks():
    e = engine()
    assert times(e, date(2026, 11, 5)) == ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
                                            "17:00", "17:30", "18:00", "18:30", "19:00", "19:30"]
    assert e.slots(date(2026, 11, 8)) == [] and not e.is_open(date(2026, 11, 8))      # Sunday


def test_a_slot_never_crosses_the_end_of_a_range():
    e = engine(make(slot_minutes=45, week=week(range(7), ("10:00", "12:00"))))
    assert times(e, date(2026, 11, 5)) == ["10:00", "10:45"]            # 11:30 would end at 12:15
    e = engine(make(slot_minutes=240, week=week(range(7), ("10:00", "13:00"))))
    assert times(e, date(2026, 11, 5)) == []


def test_the_buffer_widens_the_step_between_starts():
    e = engine(make(slot_minutes=20, buffer_minutes=10, week=week(range(7), ("10:00", "11:30"))))
    assert times(e, date(2026, 11, 5)) == ["10:00", "10:30", "11:00"]
    assert [s.end.strftime("%H:%M") for s in e.slots(date(2026, 11, 5))] == ["10:20", "10:50", "11:20"]


def test_statuses_past_blocked_full_booked_free():
    d = date(2026, 11, 4)
    bk = [booking("2026-11-04T10:30", bid="A10"), booking("2026-11-04T11:00", bid="A11"),
          booking("2026-11-04T11:00", bid="A12", name="Ravi")]
    e = engine(make(capacity=2), bk, [block("2026-11-04T12:00", "2026-11-04T12:30", "K7")])
    by = {s.start.strftime("%H:%M"): s for s in e.slots(d)}
    assert by["10:00"].status == "free" and by["10:00"].remaining == 2
    assert by["10:30"].status == "booked" and by["10:30"].remaining == 1 and by["10:30"].bookings[0]["name"] == "Asha"
    assert by["11:00"].status == "full" and by["11:00"].remaining == 0 and len(by["11:00"].bookings) == 2
    assert by["12:00"].status == "blocked" and by["12:00"].block_id == "K7" and by["12:00"].remaining == 0
    assert by["12:30"].status == "free"
    today = engine(now=datetime(2026, 11, 4, 10, 30)).slots(d)
    assert [s.status for s in today[:3]] == ["past", "past", "free"]        # a slot that has started is past


def test_cancelled_and_other_schedules_bookings_do_not_count():
    rows = [booking("2026-11-04T10:00", status="cancelled"), {**booking("2026-11-04T10:30"), "schedule_id": "S2"}]
    assert [s.status for s in engine(bookings=rows).slots(date(2026, 11, 4))[:2]] == ["free", "free"]


def test_a_block_covers_a_range_and_a_closed_date_has_no_slots():
    e = engine(blocks=[block("2026-11-04T10:00", "2026-11-04T11:30")])
    assert [s.status for s in e.slots(date(2026, 11, 4))[:4]] == ["blocked", "blocked", "blocked", "free"]
    closed = engine(make(closed_dates=["2026-11-05"]))
    assert closed.slots(date(2026, 11, 5)) == [] and not closed.is_open(date(2026, 11, 5))


def test_a_booking_counts_against_every_slot_it_overlaps():
    # the slot length was changed to 15 minutes after a 30 minute booking was made
    e = engine(make(slot_minutes=15), [booking("2026-11-04T10:00", minutes=30)])
    assert [s.status for s in e.slots(date(2026, 11, 4))[:3]] == ["full", "full", "free"]


def test_the_range_view_is_ready_for_the_api():
    out = engine().range_public(date(2026, 11, 7), date(2026, 11, 8))
    assert out["timezone"] == "Asia/Kolkata" and out["kind"] == "slots" and [d["date"] for d in out["days"]] == \
        ["2026-11-07", "2026-11-08"]
    assert out["days"][1] == {"date": "2026-11-08", "open": False, "slots": []}
    first = out["days"][0]["slots"][0]
    assert first == {"start": "2026-11-07T10:00:00+05:30", "end": "2026-11-07T10:30:00+05:30", "status": "free",
                     "remaining": 1, "bookings": [], "block_id": None}


# ---------------------------------------------------------------------------------------------- check

THURSDAY = date(2026, 11, 5)


def test_a_free_time_is_available():
    r = engine().check(day=THURSDAY, at="18:00")
    assert r["available"] and r["reason"] is None and r["before"] is None and r["after"] is None
    assert r["requested"] == {"start": "2026-11-05T18:00:00+05:30", "end": "2026-11-05T18:30:00+05:30"}
    assert r["spoken"]["en"] == "Thursday at 6 pm is free."
    assert r["spoken"]["hi"] == "गुरुवार शाम छह बजे का समय खाली है।"


def test_a_taken_time_offers_the_nearest_either_side():
    e = engine(bookings=[booking("2026-11-05T18:00")])
    r = e.check(day=THURSDAY, at="18:00")
    assert not r["available"] and r["reason"] == "full"
    assert r["before"]["start"] == "2026-11-05T17:30:00+05:30" and r["after"]["start"] == "2026-11-05T18:30:00+05:30"
    assert r["spoken"]["en"] == "Thursday at 6 pm is already taken. 5:30 pm or 6:30 pm, which would suit you?"
    assert r["spoken"]["hi"].startswith("गुरुवार शाम छह बजे") and "वाला समय भरा हुआ है। साढ़े पाँच बजे या साढ़े छह बजे, " \
                                                                  "कौन सा ठीक रहेगा?" in r["spoken"]["hi"]


def test_nothing_free_on_the_day_goes_to_the_following_and_the_previous_days():
    d = date(2026, 11, 5)
    rows = [booking(f"2026-11-05T{t}", bid=f"A{i}") for i, t in enumerate(
        ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "17:00", "17:30", "18:00", "18:30", "19:00", "19:30"])]
    e = engine(bookings=rows)
    r = e.check(day=d, at="18:00")
    assert r["before"]["start"] == "2026-11-04T19:30:00+05:30"          # the last free time of the day before
    assert r["after"]["start"] == "2026-11-06T10:00:00+05:30"           # the first of the day after
    assert "Tomorrow at 7:30 pm or Friday at 10 am" in r["spoken"]["en"]
    assert "बुधवार रात" not in r["spoken"]["hi"] and "कल शाम साढ़े सात बजे" in r["spoken"]["hi"]   # tomorrow is "कल"


def test_the_reasons():
    e = engine(bookings=[booking("2026-11-05T10:00")], blocks=[block("2026-11-05T11:00", "2026-11-05T11:30")])
    assert e.check(day=THURSDAY, at="10:00")["reason"] == "full"
    assert e.check(day=THURSDAY, at="11:00")["reason"] == "blocked"
    assert e.check(day=date(2026, 11, 8), at="11:00")["reason"] == "closed"
    assert e.check(day=THURSDAY, at="14:00")["reason"] == "outside_hours"          # the break
    assert e.check(day=THURSDAY, at="21:00")["reason"] == "outside_hours"
    assert e.check(day=THURSDAY, at="13:15")["reason"] == "outside_hours"          # the range ended at 13:00
    assert e.check(day=THURSDAY, at="10:10")["reason"] == "not_a_start"            # inside the hours, not on the grid
    assert e.check(day=date(2026, 11, 2), at="10:00")["reason"] == "past"
    soon = engine(now=datetime(2026, 11, 3, 9, 30)).check(day=TODAY, at="10:00")   # 30 minutes away, 60 are needed
    assert soon["reason"] == "too_soon" and soon["after"]["start"] == "2026-11-03T10:30:00+05:30"
    assert engine(now=datetime(2026, 11, 3, 9, 0)).check(day=TODAY, at="10:00")["available"]   # exactly the notice
    assert e.check(day=date(2026, 12, 10), at="10:00")["reason"] == "too_far"      # 30 days is the horizon
    assert e.check(day=THURSDAY)["reason"] == "unclear" and e.check()["reason"] == "unclear"
    staff = e.check(day=TODAY, at="09:30", caller=False)
    assert staff["available"] is False and staff["reason"] == "outside_hours"      # 09:30 is before opening
    assert e.check(day=TODAY, at="10:00", caller=False)["available"]               # staff skip the notice


def test_a_closed_date_is_closed_whatever_the_weekday():
    e = engine(make(closed_dates=["2026-11-05"]))
    r = e.check(day=THURSDAY, at="10:00")
    assert r["reason"] == "closed" and r["before"]["start"] == "2026-11-04T19:30:00+05:30"
    assert r["after"]["start"] == "2026-11-06T10:00:00+05:30"


def test_min_notice_and_horizon_bound_the_options():
    e = engine(make(min_notice_minutes=240, horizon_days=2))
    assert e.check(day=TODAY, at="12:30")["reason"] == "too_soon"                  # needs 13:00
    r = e.check(day=TODAY, at="12:30")
    assert r["after"]["start"] == "2026-11-03T17:00:00+05:30"                      # 13:00 is closed
    assert e.check(day=date(2026, 11, 5), at="10:00")["available"]                 # today + 2 is within the horizon
    far = e.check(day=date(2026, 11, 6), at="10:00")
    assert far["reason"] == "too_far" and far["before"]["start"] == "2026-11-05T19:30:00+05:30" and far["after"] is None
    assert "2 days ahead" in far["spoken"]["en"]


def test_nothing_free_at_all_says_so():
    e = engine(make(horizon_days=1, week=week([1, 2], ("10:00", "10:30"))),
               [booking("2026-11-03T10:00"), booking("2026-11-04T10:00", bid="A11")])
    r = e.check(day=date(2026, 11, 4), at="10:00")
    assert r["before"] is None and r["after"] is None
    assert r["spoken"]["en"].endswith("Sorry, I do not see any free time in the next 1 days.")


def test_a_time_not_on_the_grid_gets_the_neighbours():
    r = engine().check(day=THURSDAY, at="10:10")
    assert r["reason"] == "not_a_start" and r["before"]["start"] == "2026-11-05T10:00:00+05:30"
    assert r["after"]["start"] == "2026-11-05T10:30:00+05:30"
    assert r["spoken"]["en"] == "We book every 30 minutes. 10 am or 10:30 am, which would suit you?"
    assert r["requested"]["start"] == "2026-11-05T10:10:00+05:30"


def test_off_grid_on_a_twenty_minute_grid_says_so_plainly_in_both_languages():
    e = engine(make(slot_minutes=20))
    r = e.check(day=THURSDAY, at="10:30")
    assert r["reason"] == "not_a_start" and not r["available"]
    assert (r["before"]["start"], r["after"]["start"]) == ("2026-11-05T10:20:00+05:30", "2026-11-05T10:40:00+05:30")
    assert r["spoken"]["en"] == "We book every 20 minutes. 10:20 am or 10:40 am, which would suit you?"
    assert r["spoken"]["hi"] == "हम हर 20 मिनट पर बुकिंग करते हैं। दस बजकर 20 मिनट या दस बजकर 40 मिनट, कौन सा ठीक रहेगा?"


def test_off_grid_offers_the_nearest_free_ones_when_the_neighbours_are_taken():
    e = engine(make(slot_minutes=20), [booking("2026-11-05T10:20", 20), booking("2026-11-05T10:40", 20, bid="A11")])
    r = e.check(day=THURSDAY, at="10:30")
    assert r["reason"] == "not_a_start"
    assert (r["before"]["start"], r["after"]["start"]) == ("2026-11-05T10:00:00+05:30", "2026-11-05T11:00:00+05:30")


def test_the_step_includes_the_buffer_and_a_time_in_the_gap_is_off_grid():
    e = engine(make(slot_minutes=20, buffer_minutes=10))   # 10:00, 10:30, 11:00
    r = e.check(day=THURSDAY, at="10:25")
    assert r["reason"] == "not_a_start" and "every 30 minutes" in r["spoken"]["en"]


def test_unclear_is_kept_for_a_missing_date_or_time():
    e = engine(make(slot_minutes=20))
    assert e.check()["reason"] == "unclear" and e.check(day=THURSDAY)["reason"] == "unclear"


def test_a_day_without_a_time_names_the_first_free_one():
    r = engine().check(day=THURSDAY)
    assert r["reason"] == "unclear" and r["requested"] is None and r["after"]["start"] == "2026-11-05T10:00:00+05:30"
    assert r["spoken"]["en"] == "What time on Thursday? The first free time is 10 am."
    assert "day" not in r


def test_free_times_and_the_card_summary():
    e = engine(bookings=[booking("2026-11-03T10:00")])
    assert e.free_times(TODAY)[:2] == ["10:30", "11:00"]
    s = e.summary()
    assert s["today_booked"] == 1 and s["today_free"] == 11 and s["next_free"] == "2026-11-03T10:30:00+05:30"
    assert s["hours_text"] == "Mon to Sat, 10:00 to 13:00 and 17:00 to 20:00"


def test_capacity_lets_several_book_one_slot():
    e = engine(make(capacity=3), [booking("2026-11-05T10:00", bid="A1"), booking("2026-11-05T10:00", bid="A2")])
    assert e.check(day=THURSDAY, at="10:00")["available"]
    e = engine(make(capacity=3), [booking("2026-11-05T10:00", bid=f"A{i}") for i in range(3)])
    assert e.check(day=THURSDAY, at="10:00")["reason"] == "full"


# ---------------------------------------------------------------------------------------------- nights

def rooms(capacity=2, **kw):
    return make(kind="nights", capacity=capacity, check_in="12:00", check_out="11:00",
                week=week(range(7), ("00:00", "24:00")), horizon_days=30, **kw)


def stay(start_day, nights, bid="A10"):
    s = datetime.fromisoformat(start_day + "T12:00")
    return booking(s.isoformat(), minutes=0, bid=bid, nights=nights) | {
        "end": (datetime.combine(s.date() + timedelta(days=nights), datetime.min.time()) + timedelta(hours=11)).isoformat()}


def test_a_night_is_check_in_to_check_out_the_next_day():
    e = engine(rooms())
    [s] = e.slots(date(2026, 11, 6))
    assert (s.start, s.end) == (datetime(2026, 11, 6, 12), datetime(2026, 11, 7, 11))
    assert s.status == "free" and s.remaining == 2
    assert e.slots(TODAY)[0].status == "free"                  # tonight can still be booked
    assert e.slots(date(2026, 11, 2))[0].status == "past"


def test_a_stay_uses_every_night_it_covers():
    e = engine(rooms(), [stay("2026-11-06", 2, "A1"), stay("2026-11-07", 1, "A2"), stay("2026-11-07", 1, "A3")])
    by = {d: e.slots(date(2026, 11, d))[0] for d in (5, 6, 7, 8)}
    assert [by[d].status for d in (5, 6, 7, 8)] == ["free", "booked", "full", "free"]
    assert by[7].remaining == 0 and by[6].remaining == 1


def test_a_stay_needs_a_room_every_night():
    e = engine(rooms(), [stay("2026-11-07", 1, "A2"), stay("2026-11-07", 1, "A3")])
    ok = e.check(day=date(2026, 11, 5), nights=2)
    assert ok["available"] and ok["requested"] == {"start": "2026-11-05T12:00:00+05:30",
                                                  "end": "2026-11-07T11:00:00+05:30"} and ok["nights"] == 2
    no = e.check(day=date(2026, 11, 6), nights=2)
    assert no["reason"] == "full"
    assert no["before"]["start"] == "2026-11-05T12:00:00+05:30"      # nights 5 and 6 are free
    assert no["after"]["start"] == "2026-11-08T12:00:00+05:30"       # 7 is full, so the next two free nights are 8 and 9
    assert "fully booked for 2 nights from Friday" in no["spoken"]["en"]
    assert e.check(day=date(2026, 11, 6), nights=1)["available"]


def test_nights_blocked_closed_past_far():
    e = engine(rooms(closed_dates=["2026-11-10"]), blocks=[block("2026-11-08T12:00", "2026-11-09T11:00")])
    assert e.check(day=date(2026, 11, 8), nights=1)["reason"] == "blocked"
    assert e.check(day=date(2026, 11, 9), nights=1)["available"]          # the block ends at check-out
    assert e.check(day=date(2026, 11, 9), nights=2)["reason"] == "closed"
    assert e.check(day=date(2026, 11, 2), nights=1)["reason"] == "past"
    assert e.check(day=date(2027, 1, 1), nights=1)["reason"] == "too_far"
    assert e.check(nights=2)["reason"] == "unclear"
    s = e.summary()
    assert s["today_free"] == 2 and s["today_booked"] == 0 and s["next_free"] == "2026-11-03T12:00:00+05:30"
    assert s["hours_text"] == "Every day, check-in 12:00, check-out 11:00"


def test_a_stay_in_hindi():
    e = engine(rooms(), [stay("2026-11-06", 1, "A2"), stay("2026-11-06", 1, "A3")])
    r = e.check(day=date(2026, 11, 6), nights=2)
    assert "सभी कमरे भरे हुए हैं" in r["spoken"]["hi"] and "2 रात" in r["spoken"]["hi"]


# ---------------------------------------------------------------------------------------------- spoken words

@pytest.mark.parametrize("hour,minute,words", [
    (6, 0, "सुबह छह बजे"), (13, 0, "दोपहर एक बजे"), (18, 0, "शाम छह बजे"), (21, 0, "रात नौ बजे"),
    (12, 0, "दोपहर बारह बजे"), (17, 30, "शाम साढ़े पाँच बजे"), (13, 30, "दोपहर डेढ़ बजे"), (14, 30, "दोपहर ढाई बजे"),
    (10, 15, "सुबह सवा दस बजे"), (18, 45, "शाम पौने सात बजे"), (12, 45, "दोपहर पौने एक बजे"),
    (10, 10, "सुबह दस बजकर 10 मिनट"),
])
def test_hindi_clock_words(hour, minute, words):
    assert av.time_hi(datetime(2026, 11, 5, hour, minute)) == words


@pytest.mark.parametrize("hour,minute,words", [(6, 0, "6 am"), (12, 0, "noon"), (17, 30, "5:30 pm"), (0, 0, "midnight"),
                                               (23, 15, "11:15 pm"), (12, 30, "12:30 pm")])
def test_english_clock_words(hour, minute, words):
    assert av.time_en(datetime(2026, 11, 5, hour, minute)) == words


def test_day_words():
    today = date(2026, 11, 3)
    assert [av.day_en(today + timedelta(days=i), today) for i in (0, 1, 2, 6, 7)] == \
        ["today", "tomorrow", "Thursday", "Monday", "Tuesday 10 November"]
    assert [av.day_hi(today + timedelta(days=i), today) for i in (0, 1, 2, 3, 7)] == \
        ["आज", "कल", "गुरुवार", "शुक्रवार", "10 नवंबर (मंगलवार)"]


# ---------------------------------------------------------------------------------------------- properties

def random_schedule(rng):
    days = {}
    for d in sch.DAYS:
        ranges, t = [], rng.choice([0, 300, 420])
        for _ in range(rng.randint(0, 3)):
            a = t + rng.randint(0, 120)
            b = a + rng.randint(5, 300)
            if b >= 1440:
                break
            ranges.append({"start": sch.hhmm(a), "end": sch.hhmm(b)})
            t = b + rng.randint(0, 90)
        days[d] = ranges
    return make(slot_minutes=rng.choice([5, 10, 15, 20, 30, 45, 60, 90]), buffer_minutes=rng.choice([0, 0, 5, 10, 30]),
                capacity=rng.randint(1, 4), week=sch.normalise_week(days), horizon_days=rng.randint(1, 40),
                min_notice_minutes=rng.choice([0, 30, 60, 240]))


def random_bookings(rng, eng):
    out = []
    for i in range(rng.randint(0, 25)):
        d = TODAY + timedelta(days=rng.randint(0, 10))
        slots = eng.slots(d)
        if slots:
            s = rng.choice(slots)
            out.append(booking(s.start.isoformat(), minutes=int((s.end - s.start).total_seconds() // 60), bid=f"A{i}"))
    return out


@pytest.mark.parametrize("seed", range(25))
def test_slots_are_ordered_inside_their_ranges_and_never_overlap(seed):
    rng = random.Random(seed)
    s = random_schedule(rng)
    e = engine(s)
    step = s["slot_minutes"] + s["buffer_minutes"]
    for i in range(14):
        d = TODAY + timedelta(days=i)
        slots = e.slots(d)
        ranges = [(sch.to_min(r["start"]), sch.to_min(r["end"], allow_24=True)) for r in s["week"][sch.DAYS[d.weekday()]]]
        for a, b in zip(slots, slots[1:]):
            assert a.end <= b.start and b.start - a.start >= timedelta(minutes=step) or \
                not any(lo <= a.start.hour * 60 + a.start.minute < hi for lo, hi in ranges if lo <= b.start.hour * 60 + b.start.minute < hi)
        for sl in slots:
            m = sl.start.hour * 60 + sl.start.minute
            assert any(lo <= m and m + s["slot_minutes"] <= hi for lo, hi in ranges)
            assert (m - next(lo for lo, hi in ranges if lo <= m < hi)) % step == 0


@pytest.mark.parametrize("seed", range(25))
def test_options_are_free_future_slots_on_the_right_side(seed):
    rng = random.Random(1000 + seed)
    s = random_schedule(rng)
    base = engine(s)
    e = engine(s, random_bookings(rng, base))
    horizon_last = TODAY + timedelta(days=s["horizon_days"])
    for _ in range(12):
        d = TODAY + timedelta(days=rng.randint(-1, s["horizon_days"] + 3))
        at = sch.hhmm(rng.randrange(0, 1440, 5))
        r = e.check(day=d, at=at)
        asked = datetime.combine(d, datetime.min.time()) + timedelta(minutes=sch.to_min(at))
        if r["available"]:
            assert r["before"] is None and r["after"] is None and asked >= NOW + timedelta(minutes=s["min_notice_minutes"])
            slot = next(x for x in e.slots(d) if x.start == asked)
            assert slot.remaining > 0 and slot.status in ("free", "booked")
            continue
        assert r["reason"] in ("closed", "full", "blocked", "past", "outside_hours", "too_soon", "too_far", "not_a_start", "unclear")
        for key, sign in (("before", -1), ("after", 1)):
            opt = r[key]
            if opt is None:
                continue
            start = datetime.fromisoformat(opt["start"]).replace(tzinfo=None)
            assert (start - asked) * sign > timedelta(0)
            assert start >= NOW + timedelta(minutes=s["min_notice_minutes"]) and start.date() <= horizon_last
            slot = next(x for x in e.slots(start.date()) if x.start == start)
            assert slot.status in ("free", "booked") and slot.remaining > 0
        # nothing nearer was skipped: no bookable slot lies strictly between the asked time and an option
        if r["after"]:
            a = datetime.fromisoformat(r["after"]["start"]).replace(tzinfo=None)
            d2 = asked.date()
            while d2 <= a.date():
                assert not [x for x in e.bookable(d2) if asked < x.start < a]
                d2 += timedelta(days=1)


def test_the_answers_are_deterministic_and_fast():
    import time
    e = engine(make(horizon_days=365, week=week(range(7), ("00:00", "24:00")), slot_minutes=5))
    started = time.perf_counter()
    r1 = e.check(day=date(2027, 6, 1), at="12:00")
    again = engine(make(horizon_days=365, week=week(range(7), ("00:00", "24:00")), slot_minutes=5)).check(
        day=date(2027, 6, 1), at="12:00")
    assert r1 == again and json.dumps(r1)
    assert time.perf_counter() - started < 1.0


def test_a_sentence_starts_with_a_capital_even_when_it_begins_with_a_relative_day():
    e = engine(bookings=[booking("2026-11-04T18:00")])
    assert e.check(day=date(2026, 11, 4), at="18:00")["spoken"]["en"].startswith("Tomorrow at 6 pm is already taken.")
    assert engine().check(day=date(2026, 11, 4), at="18:00")["spoken"]["en"] == "Tomorrow at 6 pm is free."
