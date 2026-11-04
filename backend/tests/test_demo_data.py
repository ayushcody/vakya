"""The pure parts of backend/scripts/demo_data.py (./vakya demo): opening hours, choosing slots, the backup and finding the
demo desks that already exist. Temporary directories only; the real backend/data is never touched and no API is called."""
import random
from datetime import date, datetime, timedelta

import pytest

from scripts import demo_data as dd

WEEK = {d: [{"start": "10:00", "end": "13:00"}, {"start": "17:00", "end": "20:00"}] for d in dd.DAYS[:6]} | {"sun": []}
SCHEDULE = {"id": "S1", "kind": "slots", "slot_minutes": 15, "capacity": 1, "week": WEEK, "closed_dates": []}
TODAY = date(2026, 11, 4)                        # a Wednesday
NOW = datetime(2026, 11, 4, 11, 0)


def day_of(d, schedule=SCHEDULE, busy=(), extra=()):
    """The calendar of one day as /api/schedules/{id}/slots gives it."""
    slots = []
    for a, b in dd.open_ranges(schedule, d):
        t = a
        while t + schedule["slot_minutes"] <= b:
            start = datetime.combine(d, datetime.min.time()) + timedelta(minutes=t)
            end = start + timedelta(minutes=schedule["slot_minutes"])
            slots.append({"start": start.isoformat() + "+05:30", "end": end.isoformat() + "+05:30",
                          "status": "booked" if start.strftime("%H:%M") in busy else "free",
                          "remaining": 0 if start.strftime("%H:%M") in busy else 1, "block_id": None})
            t += schedule["slot_minutes"]
    return {"date": d.isoformat(), "open": bool(slots), "slots": slots + list(extra)}


def week_of(schedule=SCHEDULE, n=7):
    return [day_of(TODAY + timedelta(days=i), schedule) for i in range(n)]


# ------------------------------------------------------------------------------------------------ opening hours

def test_open_ranges_follow_the_weekday_and_closed_dates():
    assert dd.open_ranges(SCHEDULE, date(2026, 11, 4)) == [(600, 780), (1020, 1200)]
    assert dd.open_ranges(SCHEDULE, date(2026, 11, 8)) == []                       # Sunday
    closed = {**SCHEDULE, "closed_dates": ["2026-11-05"]}
    assert dd.open_ranges(closed, date(2026, 11, 5)) == []


def test_inside_hours_needs_the_whole_slot_in_one_range():
    assert dd.inside_hours(SCHEDULE, datetime(2026, 11, 4, 10, 0))
    assert dd.inside_hours(SCHEDULE, datetime(2026, 11, 4, 12, 45))                # 12:45 to 13:00
    assert not dd.inside_hours(SCHEDULE, datetime(2026, 11, 4, 13, 0))             # the lunch break
    assert not dd.inside_hours(SCHEDULE, datetime(2026, 11, 4, 12, 50))            # runs past 13:00
    assert not dd.inside_hours(SCHEDULE, datetime(2026, 11, 4, 9, 45))
    assert not dd.inside_hours(SCHEDULE, datetime(2026, 11, 8, 11, 0))             # Sunday


def test_free_slots_skip_closed_days_taken_slots_past_times_and_blocks():
    sunday = day_of(date(2026, 11, 8))
    assert dd.free_slots(sunday, SCHEDULE, NOW) == []
    today = day_of(TODAY, busy=("11:15",))
    got = dd.free_slots(today, SCHEDULE, NOW)
    assert got and all(dd.naive(s) > NOW for s in got)                             # nothing already past
    assert "2026-11-04T11:15:00+05:30" not in got                                   # taken
    blocked = day_of(TODAY)
    next(s for s in blocked["slots"] if s["start"].startswith("2026-11-04T17:00"))["block_id"] = "K1"
    assert "2026-11-04T17:00:00+05:30" not in dd.free_slots(blocked, SCHEDULE, NOW)


def test_free_slots_drop_a_slot_outside_the_hours_even_if_the_api_listed_it():
    stray = {"start": "2026-11-04T14:00:00+05:30", "end": "2026-11-04T14:15:00+05:30", "status": "free",
             "remaining": 1, "block_id": None}
    assert "2026-11-04T14:00:00+05:30" not in dd.free_slots(day_of(TODAY, extra=[stray]), SCHEDULE, NOW)


# ------------------------------------------------------------------------------------------------ choosing slots

def test_pick_slots_spread_over_days_inside_hours_and_skip_sunday():
    picks = dd.pick_slots(week_of(), SCHEDULE, NOW, random.Random(1))
    firsts = [p[0] for p in picks]
    assert 10 <= len(firsts) <= 14
    assert len(set(firsts)) == len(firsts)                                         # never the same slot twice
    days = [dd.naive(f).date() for f in firsts]
    assert date(2026, 11, 8) not in days                                           # Sunday
    assert days.count(TODAY) == 3 and days.count(date(2026, 11, 5)) == 2
    assert all(dd.naive(f) > NOW and dd.inside_hours(SCHEDULE, dd.naive(f)) for f in firsts)
    assert all(len(p) > 1 and all(a[:10] == p[0][:10] for a in p) for p in picks)  # alternates are from the same day


def test_pick_slots_is_repeatable_for_a_seed_and_carries_a_shortfall_forward():
    assert dd.pick_slots(week_of(), SCHEDULE, NOW, random.Random(5)) == dd.pick_slots(week_of(), SCHEDULE, NOW, random.Random(5))
    late = datetime(2026, 11, 4, 19, 50)                                           # nearly closing time today
    picks = dd.pick_slots(week_of(), SCHEDULE, late, random.Random(2))
    assert len(picks) >= 10                                                        # the missing ones move to later days


def test_pick_slots_can_be_limited_to_some_dates():
    only = ["2026-11-06"]
    picks = dd.pick_slots(week_of(), SCHEDULE, NOW, random.Random(3), only_days=only)
    assert picks and all(p[0].startswith("2026-11-06") for p in picks)


def test_find_block_window_gives_a_free_run_after_today_and_tail_lands_at_the_end_of_the_session():
    days = week_of()
    start, end = dd.find_block_window(days, SCHEDULE, NOW, minutes=60)
    assert start == "2026-11-05T10:00:00+05:30" and end == "2026-11-05T11:00:00+05:30"
    start, end = dd.find_block_window(days, SCHEDULE, NOW, minutes=60, tail=True)
    assert (start, end) == ("2026-11-05T12:00:00+05:30", "2026-11-05T13:00:00+05:30")
    start, end = dd.find_block_window(days, SCHEDULE, NOW, avoid_days=["2026-11-05"], minutes=45)
    assert start.startswith("2026-11-06")


def test_find_block_window_skips_busy_slots():
    days = week_of()
    days[1] = day_of(date(2026, 11, 5), busy=("10:30",))
    start, _ = dd.find_block_window(days, SCHEDULE, NOW, minutes=60)
    assert start == "2026-11-05T10:45:00+05:30"


def test_pick_stays_are_spread_over_two_weeks_with_one_to_three_nights():
    stays = dd.pick_stays(TODAY, random.Random(7))
    assert 4 <= len(stays) <= dd.STAYS
    dates = [s["check_in"] for s in stays]
    assert dates == sorted(dates) and all(TODAY <= d <= TODAY + timedelta(days=dd.STAY_HORIZON_DAYS) for d in dates)
    assert all(b - a >= timedelta(days=2) for a, b in zip(dates, dates[1:]))
    assert all(1 <= s["nights"] <= 3 and 1 <= s["party"] <= 4 for s in stays)


# ------------------------------------------------------------------------------------------------ the content

def test_names_are_invented_with_devanagari_and_phones_use_the_fictional_block():
    assert sum(any("ऀ" <= c <= "ॿ" for c in n) for n in dd.NAMES) >= 2
    assert dd.phone_for(1) == "+919876500001" and dd.phone_for(14) == "+919876500014"
    assert len(set(dd.phone_for(i) for i in range(1, 200))) == 199
    nxt = dd.names_cycle(random.Random(1))
    got = [nxt() for _ in range(len(dd.NAMES))]
    assert sorted(got) == sorted(dd.NAMES)


# ------------------------------------------------------------------------------------------------ the backup

def test_make_backup_copies_the_data_and_leaves_it_alone(tmp_path):
    data = tmp_path / "data"
    (data / "local_db").mkdir(parents=True)
    (data / "local_db" / "agents.json").write_text('{"keep": 1}')
    (data / "schedules.json").write_text("{}")
    root = tmp_path / "data-backups"
    when = datetime(2026, 11, 4, 8, 11)
    dest = dd.make_backup(data, root, when)
    assert dest == (root / "2026-11-04-0811").resolve()
    assert (dest / "local_db" / "agents.json").read_text() == '{"keep": 1}'
    assert (data / "local_db" / "agents.json").read_text() == '{"keep": 1}'        # the original is untouched
    again = dd.make_backup(data, root, when)                                       # same minute: a new folder, no overwrite
    assert again != dest and again.name == "2026-11-04-0811-2" and (dest / "schedules.json").exists()


def test_make_backup_of_a_missing_folder_is_an_empty_folder(tmp_path):
    dest = dd.make_backup(tmp_path / "nothing", tmp_path / "b", datetime(2026, 1, 2, 3, 4))
    assert dest.is_dir() and list(dest.iterdir()) == []


def test_make_backup_refuses_a_folder_inside_the_data(tmp_path):
    (tmp_path / "data").mkdir()
    with pytest.raises(ValueError):
        dd.make_backup(tmp_path / "data", tmp_path / "data" / "backups", datetime(2026, 1, 1))


def test_the_backup_root_is_ignored_by_git_and_sits_beside_data():
    assert dd.DEFAULT_BACKUP_ROOT.name == "data-backups" and dd.DEFAULT_BACKUP_ROOT.parent == dd.DEFAULT_DATA_DIR.parent
    ignore = (dd.BACKEND.parent / ".gitignore").read_text()
    assert "backend/data-backups/" in ignore


# ------------------------------------------------------------------------------------------------ idempotency

def test_find_demo_agents_matches_by_organisation_name():
    agents = [{"id": "a1", "name": "Green Valley Clinic Reception"}, {"id": "a2", "name": "Someone Else Front Desk"}]
    found = dd.find_demo_agents(agents)
    assert found["clinic"]["id"] == "a1" and found["hotel"] is None
    assert dd.find_demo_agents([])["clinic"] is None
    assert dd.find_demo_agents([{"id": "x", "name": "GREEN VALLEY CLINIC reception"}])["clinic"]["id"] == "x"


def test_empty_days_lists_open_days_without_a_standing_booking():
    days = week_of()
    rows = [{"schedule_id": "S1", "date": "2026-11-04", "status": "booked"},
            {"schedule_id": "S1", "date": "2026-11-05", "status": "cancelled"},       # cancelled does not count
            {"schedule_id": "S2", "date": "2026-11-06", "status": "booked"}]          # another schedule
    got = dd.empty_days(rows, "S1", days)
    assert "2026-11-04" not in got and "2026-11-05" in got and "2026-11-06" in got
    assert "2026-11-08" not in got                                                    # Sunday is closed


class FakeApi:
    """Records what a run would send; GET answers come from `state`."""
    def __init__(self, state):
        self.state, self.sent = state, []

    def get(self, path):
        return self.state[path.split("?")[0]]

    def post(self, path, body):
        self.sent.append((path, body))
        raise AssertionError("a run with every desk present must not write")


def test_a_run_with_the_demo_desks_present_writes_nothing_and_makes_no_backup(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    state = {"/api/agents": {"agents": [{"id": "a1", "name": "Green Valley Clinic Reception"},
                                         {"id": "a2", "name": "Palm Grove Residency Front Desk"}]},
             "/api/schedules": {"schedules": [{"id": "S1", "name": "Doctor consultations", "kind": "slots", "week": WEEK}]},
             "/api/bookings": {"bookings": [{"schedule_id": "S1", "date": "2026-11-04", "status": "booked"}]}}
    api = FakeApi(state)
    lines = []
    demo = dd.Demo(api, data, tmp_path / "data-backups", "http://localhost:3010", out=lines.append, now=lambda: NOW)
    assert demo.run(add_bookings=False) == 0
    assert api.sent == [] and not (tmp_path / "data-backups").exists()
    assert any("already exists" in line for line in lines) and any("/desk/bookings" in line for line in lines)


def test_the_script_says_to_start_the_app_when_the_api_is_down(capsys):
    assert dd.main(["--api", "http://127.0.0.1:9"]) == 2
    assert "./vakya start" in capsys.readouterr().err
