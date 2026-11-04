#!/usr/bin/env python3
"""Fill an empty desk with demo data so the Bookings pages never open empty.

    ./vakya demo                   create the demo front desks and their bookings (adds only; safe to run twice)
    ./vakya demo --add-bookings    also top up bookings on days that have none, on desks made earlier by this command

What it makes, through the running API, exactly as the desk's own forms do:
  * a clinic front desk and a hotel front desk (from the clinic and hotel templates, which also create their schedules),
  * staff-made bookings (source "staff") on the clinic's time slots over today and the next six days, two blocked
    ranges, and a handful of stays on the hotel's nightly schedule.
Nothing here is a phone call: no calls, transcripts, proof of booking, receipts or caller profiles are created, and every
name, phone number and business is invented (the numbers come from the +91 98765 00xxx block used by the tests).

It only ADDS. Before it writes anything it copies backend/data/ to backend/data-backups/<YYYY-MM-DD-HHMM>/ and prints
that path. If a desk with the demo organisation name exists, no second one is made.

The pure parts (opening hours, choosing slots, the backup, finding the existing desks) are plain functions at the top;
the HTTP part is at the bottom."""
import argparse
import json
import os
import random
import shutil
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BACKEND / "data"
DEFAULT_BACKUP_ROOT = BACKEND / "data-backups"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ZONE = "Asia/Kolkata"

# ----------------------------------------------------------------------------------------------------- the content

DESKS = (
    {"key": "clinic", "template_id": "clinic", "org": "Green Valley Clinic", "city": "Pune",
     "details": "Monday to Saturday, 10 am to 1 pm and 5 pm to 8 pm; closed on Sunday."},
    {"key": "hotel", "template_id": "hotel", "org": "Palm Grove Residency", "city": "Nashik",
     "details": "Check-in from 12 noon, check-out by 11 am; rooms with and without air conditioning."},
)

# Invented names: Hindi, Marathi and English-leaning, some written in Devanagari.
NAMES = (
    "Anita Deshpande", "आरती देशमुख", "Rohit Patil", "Imran Shaikh", "Sneha Kulkarni", "राजेश शिंदे", "Pooja Joshi",
    "Vikram Jadhav", "Neha Gokhale", "सुनीता गायकवाड", "Arjun Mehta", "Farah Sayyed", "Kavita More", "अमोल चव्हाण",
    "Sachin Pawar", "Divya Nair", "Manoj Bhosale", "प्रिया वर्मा", "Ritu Verma", "Suresh Kamble", "Meena Iyer",
    "विजय कदम", "Tanvi Salunkhe", "Harish Rao",
)
CLINIC_NOTES = (
    "Follow-up", "Fever since two days", "First visit", "Blood pressure check", "Cough and cold", "Reports to show",
    "Child vaccination", "Knee pain", "Skin rash", "Diabetes review", "बुखार दो दिन से", "Back pain, second visit",
    "Prescription refill",
)
STAY_NOTES = (
    "Late check-in, around 10 pm", "Needs a ground-floor room", "Airport pickup requested", "Travelling with a child",
    "Wedding guest", "Business trip, needs an invoice", "Anniversary stay", "Vegetarian breakfast",
)
BLOCK_REASONS = ("Doctor's lunch meeting", "Hospital rounds", "Staff training")
DAY_PLAN = (3, 2, 2, 2, 2, 1, 1)      # bookings wanted per day offset 0 (today) to 6
STAYS = 5
STAY_HORIZON_DAYS = 14
PHONE_PREFIX = "+91987650"            # +91 98765 00xxx: the made-up block the tests use


def phone_for(n: int) -> str:
    """A made-up number from the tests' fictional block (+91 98765 00001 and up)."""
    return f"{PHONE_PREFIX}{n % 10000:04d}"


# ------------------------------------------------------------------------------------------------------ pure parts

def make_backup(data_dir: Path, backup_root: Path, now: datetime) -> Path:
    """Copy `data_dir` to <backup_root>/<YYYY-MM-DD-HHMM> (-2, -3 ... when that name is taken) and return the new path.
    Nothing in `data_dir` is touched. A missing data directory gives an empty backup folder."""
    data_dir, backup_root = Path(data_dir).resolve(), Path(backup_root).resolve()
    if backup_root == data_dir or data_dir in backup_root.parents:
        raise ValueError("The backup folder must not be inside the data folder.")
    stem = now.strftime("%Y-%m-%d-%H%M")
    dest, n = backup_root / stem, 1
    while dest.exists():
        n += 1
        dest = backup_root / f"{stem}-{n}"
    backup_root.mkdir(parents=True, exist_ok=True)
    if data_dir.is_dir():
        shutil.copytree(data_dir, dest)
    else:
        dest.mkdir()
    return dest


def parse_minutes(value: str) -> int:
    h, m = str(value).split(":")[:2]
    return int(h) * 60 + int(m)


def open_ranges(schedule: dict, day: date) -> List[Tuple[int, int]]:
    """The opening ranges of `day` as (start minute, end minute); empty on a closed weekday or a closed date."""
    if day.isoformat() in set(schedule.get("closed_dates") or []):
        return []
    return [(parse_minutes(r["start"]), parse_minutes(r["end"])) for r in (schedule.get("week") or {}).get(DAYS[day.weekday()]) or []]


def inside_hours(schedule: dict, start: datetime, minutes: Optional[int] = None) -> bool:
    """True when a booking starting at `start` (naive local time) lasting `minutes` (the slot length by default) fits
    inside one opening range of its day."""
    length = int(minutes if minutes is not None else schedule.get("slot_minutes") or 0)
    s = start.hour * 60 + start.minute
    return any(a <= s and s + length <= b for a, b in open_ranges(schedule, start.date()))


def naive(iso: str) -> datetime:
    """An ISO time from the API (with the +05:30 offset) as a naive local datetime."""
    return datetime.fromisoformat(iso).replace(tzinfo=None)


def free_slots(day: dict, schedule: dict, now: datetime) -> List[str]:
    """The start times (ISO, as the API gave them) of the slots of one calendar day that staff could book: the day is
    open, the slot has room, it is still ahead of `now` and it lies inside the schedule's opening hours."""
    if not day.get("open"):
        return []
    out = []
    for s in day.get("slots") or []:
        if s.get("status") not in ("free", "booked") or int(s.get("remaining") or 0) <= 0 or s.get("block_id"):
            continue
        start = naive(s["start"])
        if start > now and inside_hours(schedule, start):
            out.append(s["start"])
    return out


def pick_slots(days: Sequence[dict], schedule: dict, now: datetime, rng: random.Random,
               plan: Sequence[int] = DAY_PLAN, only_days: Optional[Sequence[str]] = None) -> List[List[str]]:
    """What to book on a time-slot schedule: one list per booking, the first entry the slot to try and the rest the
    other free slots of that day (the next ones to try when it turns out taken). `days` is the calendar from
    /api/schedules/{id}/slots starting today; `plan` says how many bookings each day offset wants. A closed day gets
    none, and a day with too few free slots gives its shortfall to the next day. `only_days` limits it to those dates."""
    out: List[List[str]] = []
    carry = 0
    for offset, day in enumerate(days):
        want = (plan[offset] if offset < len(plan) else 0)
        if only_days is not None and day.get("date") not in only_days:
            continue
        free = free_slots(day, schedule, now)
        if not free:
            carry += want if day.get("open") else 0      # an open day with nothing left passes its share on; a closed day drops it
            continue
        take = min(len(free), want + carry)
        carry = want + carry - take
        chosen = rng.sample(free, take)
        rest = [s for s in free if s not in chosen]
        rng.shuffle(rest)
        out += [[c] + rest for c in chosen]
    return out


def _minutes_between(a: str, b: str) -> float:
    return (naive(b) - naive(a)).total_seconds() / 60


def find_block_window(days: Sequence[dict], schedule: dict, now: datetime, avoid_days: Sequence[str] = (),
                      minutes: int = 60, tail: bool = False) -> Optional[Tuple[str, str]]:
    """The first run of back-to-back free slots, at least `minutes` long, on a day after today that is open and not in
    `avoid_days`: (start, end) as ISO strings, or None. The window is the start of the run, or with `tail` its end (so a
    lunch meeting lands at the close of a morning session)."""
    for day in days:
        if day.get("date") in avoid_days or not day.get("open") or date.fromisoformat(day["date"]) <= now.date():
            continue
        runs: List[List[dict]] = []
        for s in day.get("slots") or []:
            ok = s.get("status") == "free" and not s.get("block_id") and inside_hours(schedule, naive(s["start"]))
            if not ok:
                runs.append([])
            elif runs and runs[-1] and runs[-1][-1]["end"] == s["start"]:
                runs[-1].append(s)
            else:
                runs.append([s])
        for run in runs:
            if not run or _minutes_between(run[0]["start"], run[-1]["end"]) < minutes:
                continue
            seq = run[::-1] if tail else run
            chosen: List[dict] = []
            for s in seq:
                chosen.append(s)
                if _minutes_between(min(c["start"] for c in chosen), max(c["end"] for c in chosen)) >= minutes:
                    break
            return min(c["start"] for c in chosen), max(c["end"] for c in chosen)
    return None


def pick_stays(today: date, rng: random.Random, count: int = STAYS, horizon: int = STAY_HORIZON_DAYS) -> List[dict]:
    """Stays to book: check-in dates spread over the next `horizon` days (at least two days apart), 1 to 3 nights each."""
    offsets = sorted(rng.sample(range(0, horizon - 3, 2), min(count, len(range(0, horizon - 3, 2)))))
    return [{"check_in": today + timedelta(days=o), "nights": rng.choice((1, 2, 2, 3)), "party": rng.choice((1, 2, 2, 3, 4))}
            for o in offsets]


def find_demo_agents(agents: Sequence[dict], desks: Sequence[dict] = DESKS) -> Dict[str, Optional[dict]]:
    """For each demo desk (by key), the existing agent whose name carries the demo organisation name, or None."""
    out: Dict[str, Optional[dict]] = {}
    for d in desks:
        wanted = d["org"].casefold()
        out[d["key"]] = next((a for a in agents if wanted in str(a.get("name") or "").casefold()), None)
    return out


def live(bookings: Sequence[dict], schedule_id: str) -> List[dict]:
    return [b for b in bookings if b.get("schedule_id") == schedule_id and b.get("status") != "cancelled"]


def empty_days(bookings: Sequence[dict], schedule_id: str, days: Sequence[dict]) -> List[str]:
    """The dates among `days` that are open and have no standing booking on this schedule."""
    taken = {b.get("date") for b in live(bookings, schedule_id)}
    return [d["date"] for d in days if d.get("open") and d["date"] not in taken]


def names_cycle(rng: random.Random, start: int = 0) -> Callable[[], str]:
    """A function giving the next invented name; the pool is shuffled once and never repeats until it has run out."""
    pool = list(NAMES)
    rng.shuffle(pool)
    state = {"i": start}

    def nxt() -> str:
        name = pool[state["i"] % len(pool)]
        state["i"] += 1
        return name
    return nxt


# --------------------------------------------------------------------------------------------------- the API part

class ApiError(Exception):
    def __init__(self, status: int, body: Any):
        self.status, self.body = status, body
        err = body.get("error") if isinstance(body, dict) else None
        self.code = (err.get("code") if isinstance(err, dict) else None) or (body.get("code") if isinstance(body, dict) else None)
        super().__init__(f"HTTP {status}: {json.dumps(body, ensure_ascii=False)[:300]}")


class Api:
    def __init__(self, base: str, timeout: float = 180.0):
        self.base, self.timeout = base.rstrip("/"), timeout

    def call(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data is not None else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as e:
            try:
                parsed = json.loads(e.read().decode("utf-8"))
            except ValueError:
                parsed = {"detail": e.reason}
            raise ApiError(e.code, parsed) from None

    def get(self, path: str) -> Any:
        return self.call("GET", path)

    def post(self, path: str, body: dict) -> Any:
        return self.call("POST", path, body)

    def reachable(self) -> bool:
        try:
            with urllib.request.urlopen(self.base + "/api/health", timeout=4) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False


class Demo:
    def __init__(self, api: Api, data_dir: Path, backup_root: Path, web: str, out: Callable[[str], None] = print,
                 now: Optional[Callable[[], datetime]] = None):
        self.api, self.data_dir, self.backup_root, self.web, self.out = api, data_dir, backup_root, web.rstrip("/"), out
        self._now = now or (lambda: datetime.now(ZoneInfo(ZONE)).replace(tzinfo=None, second=0, microsecond=0))
        self.backup_path: Optional[Path] = None
        self.made = {"desks": 0, "schedules": 0, "bookings": 0, "blocks": 0}
        self.per_schedule: List[dict] = []
        self.phone_n = 0
        self.add_bookings = False

    # Nothing is written until this has run once: the backup comes first, and is printed.
    def before_writing(self) -> None:
        if self.backup_path is None:
            self.backup_path = make_backup(self.data_dir, self.backup_root, self._now())
            self.out(f"Backup of backend/data made first: {self.backup_path}")

    def book(self, schedule: dict, candidates: Sequence[str], name: str, note: str, nights: Optional[int] = None,
             party: Optional[int] = None) -> Optional[str]:
        """Create one staff booking at the first candidate the API accepts (a 409 moves to the next one). Returns the
        start that worked or None."""
        self.before_writing()
        for start in list(candidates)[:12]:
            self.phone_n += 1
            body: Dict[str, Any] = {"schedule_id": schedule["id"], "start": start, "name": name,
                                    "phone": phone_for(self.phone_n), "note": note}
            if nights:
                body["nights"] = nights
            if party:
                body["party"] = party
            try:
                self.api.post("/api/bookings", body)
            except ApiError as e:
                if e.status == 409:
                    continue
                raise
            self.made["bookings"] += 1
            return start
        return None

    def calendar(self, schedule: dict, days: int) -> List[dict]:
        today = self._now().date()
        q = f"?from={today.isoformat()}&to={(today + timedelta(days=days - 1)).isoformat()}"
        return self.api.get(f"/api/schedules/{schedule['id']}/slots{q}")["days"]

    def fill_slots(self, schedule: dict, rng: random.Random, only_days: Optional[Sequence[str]] = None) -> int:
        now = self._now()
        names = names_cycle(rng, self.phone_n)
        wanted = pick_slots(self.calendar(schedule, 7), schedule, now, rng, only_days=only_days)
        done, taken = 0, set()
        for i, candidates in enumerate(wanted):
            party = 2 if i % 6 == 4 else None
            note = CLINIC_NOTES[(i + rng.randrange(len(CLINIC_NOTES))) % len(CLINIC_NOTES)]
            got = self.book(schedule, [c for c in candidates if c not in taken], names(), note, party=party)
            if got:
                taken.add(got)
                done += 1
        return done

    def fill_full_slot(self, schedule: dict, rng: random.Random) -> int:
        """A schedule that takes several people at once gets one slot booked to its capacity."""
        cap = int(schedule.get("capacity") or 1)
        if schedule.get("kind") != "slots" or cap < 2:
            return 0
        days = self.calendar(schedule, 7)
        picks = pick_slots(days[1:], schedule, self._now(), rng, plan=(1, 1, 1, 1, 1, 1))
        if not picks:
            return 0
        names = names_cycle(rng, self.phone_n + 7)
        n = 0
        for _ in range(cap):
            if self.book(schedule, picks[0][:1], names(), "Group visit"):
                n += 1
        return n

    def add_blocks(self, schedule: dict, rng: random.Random, existing: int = 0) -> int:
        made = 0
        used: List[str] = []
        for reason in BLOCK_REASONS[:max(0, 2 - existing)]:
            days = self.calendar(schedule, 7)
            window = find_block_window(days, schedule, self._now(), avoid_days=used,
                                       minutes=60 if reason != BLOCK_REASONS[1] else 45, tail=reason == BLOCK_REASONS[0])
            if not window:
                continue
            self.before_writing()
            self.api.post(f"/api/schedules/{schedule['id']}/blocks", {"start": window[0], "end": window[1], "reason": reason})
            used.append(window[0][:10])
            made += 1
        self.made["blocks"] += made
        return made

    def fill_stays(self, schedule: dict, rng: random.Random, count: int = STAYS) -> int:
        names = names_cycle(rng, self.phone_n + 3)
        done = 0
        for i, stay in enumerate(pick_stays(self._now().date(), rng, count)):
            # A refused night moves the check-in one day later, up to four tries.
            starts = [f"{stay['check_in'] + timedelta(days=k):%Y-%m-%d}T12:00" for k in range(4)]
            if self.book(schedule, starts, names(), STAY_NOTES[(i + rng.randrange(len(STAY_NOTES))) % len(STAY_NOTES)],
                         nights=stay["nights"], party=stay["party"]):
                done += 1
        return done

    # ------------------------------------------------------------------------------------------------------ the run

    def run(self, add_bookings: bool) -> int:
        self.add_bookings = add_bookings
        today = self._now().date()
        agents = self.api.get("/api/agents").get("agents", [])
        found = find_demo_agents(agents)
        for desk in DESKS:
            rng = random.Random(f"{desk['org']}:{today.isoformat()}")
            agent = found[desk["key"]]
            if agent is None:
                self.before_writing()
                self.out(f"Creating {desk['org']} ({desk['template_id']} template)...")
                agent = self.api.post("/api/agents/from-template", {
                    "template_id": desk["template_id"], "org_name": desk["org"], "city": desk["city"],
                    "details": desk["details"]})
                self.made["desks"] += 1
                scheds = agent.get("schedules") or []
                self.made["schedules"] += len(scheds)
                fresh = True
            else:
                self.out(f"{desk['org']} already exists ({agent.get('name')}); not creating a second one.")
                scheds = self.api.get(f"/api/schedules?agent_id={agent['id']}").get("schedules", [])
                fresh = False
            rows = self.api.get("/api/bookings")["bookings"]
            for s in scheds:
                before = len(live(rows, s["id"]))
                added = 0
                if s.get("kind") == "nights":
                    if fresh or (add_bookings and before < 4):
                        added = self.fill_stays(s, rng, STAYS if fresh else 4 - before)
                else:
                    if fresh:
                        added = self.fill_slots(s, rng) + self.fill_full_slot(s, rng)
                        self.add_blocks(s, rng)
                    elif add_bookings:
                        empty = empty_days(rows, s["id"], self.calendar(s, 7))
                        added = self.fill_slots(s, rng, only_days=empty) if empty else 0
                self.per_schedule.append({"desk": agent.get("name"), "schedule": s["name"], "id": s["id"],
                                          "kind": s.get("kind"), "before": before, "added": added})
        self.summary()
        return 0

    def summary(self) -> None:
        agents = self.api.get("/api/agents").get("agents", [])
        rows = self.api.get("/api/bookings")["bookings"]
        self.out("")
        self.out("Demo data")
        for r in self.per_schedule:
            total = len(live(rows, r["id"]))
            self.out(f"  {r['desk']}: {r['schedule']} ({r['id']}) has {total} bookings"
                     + (f", {r['added']} added now" if r["added"] else ""))
        self.out(f"  Made now: {self.made['desks']} desks, {self.made['schedules']} schedules, "
                 f"{self.made['bookings']} bookings, {self.made['blocks']} blocked ranges "
                 f"({len(agents)} desks in all)")
        if self.backup_path:
            self.out(f"  Backup: {self.backup_path}")
        else:
            self.out("  Nothing needed adding, so nothing was written and no backup was made."
                     + ("" if self.add_bookings else " Add --add-bookings to top up days with no bookings."))
        self.out(f"  Open {self.web}/desk/bookings")
        for r in self.per_schedule:
            self.out(f"       {self.web}/desk/bookings/{r['id']}   ({r['schedule']})")


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Create demo front desks and staff bookings through the running API.")
    p.add_argument("--api", default=os.getenv("VAKYA_DEMO_API", "http://localhost:8020"))
    p.add_argument("--web", default=os.getenv("VAKYA_DEMO_WEB", "http://localhost:3010"))
    p.add_argument("--add-bookings", action="store_true", help="top up days with no bookings on the existing demo desks")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    p.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    args = p.parse_args(argv)
    api = Api(args.api)
    if not api.reachable():
        print(f"The Vakya API is not running at {args.api}. Run ./vakya start first, then ./vakya demo.", file=sys.stderr)
        return 2
    try:
        return Demo(api, Path(args.data_dir), Path(args.backup_root), args.web).run(args.add_bookings)
    except ApiError as e:
        print(f"The API refused a request: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
