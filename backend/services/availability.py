"""Is this time free? The one place that answers it, for the calendar, the "ask like a caller" tester, the staff booking and
the agent's tools alike.

`Engine(schedule, bookings, blocks, now)` is built from plain data (no files, no clock of its own), so every rule is a
deterministic function of its inputs. Dates and times are naive local datetimes of the schedule's timezone.

Slots (kind "slots"): inside each open range the first slot starts when the range does; the next starts `slot_minutes +
buffer_minutes` later; a slot must end inside its range (so a break, a closing time or a range end ends the run). A slot's
status is, in this order:
    past     its start is not after now (nights: the night's date is before today)
    blocked  a block overlaps it (the block takes the whole capacity)
    full     as many live bookings overlap it as the capacity
    booked   at least one booking, room left
    free
`remaining` is what is left of the capacity (0 when blocked). A booking counts against every slot it overlaps, so a schedule
whose slot length changed later stays consistent.

Nights (kind "nights"): one slot per date, from check-in that day to check-out the next. A stay of N nights from date D
uses the nights D .. D+N-1, each needing a free room. A block blocks the nights it overlaps.

`check()` answers a request (a date and time, or a check-in date and a number of nights) with `available`, a `reason`
when not (closed, full, blocked, calendar_busy, past, outside_hours, too_soon, too_far, not_a_start, unclear) and the nearest free slot before and
after, searched on the same day first and then on the days either side (forwards up to the horizon). A caller on the
phone is held to the minimum notice and the horizon; staff are not (`caller=False`).

Not modelled: party size (it is recorded on a booking, but one booking uses one unit of capacity), time zones that change
their clocks, ranges that cross midnight (use two days).

The owner's own calendar (docs/CALENDAR.md): when Google Calendar is set up, `external(day)` gives the busy intervals of a day
from the owner's own events, and a slot they overlap is "blocked" with `calendar=True` (reason "calendar_busy"). It applies to
time slots only (a stay is not blocked by a meeting). If Google cannot be read, the Engine goes on from Vakya's own list for
the rest of its life and says so in the log."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

try:
    from services import schedules as sch
except ImportError:  # pragma: no cover
    from backend.services import schedules as sch

WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKDAYS_HI = ("सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार")
MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December")
MONTHS_HI = ("जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर")
HOURS_HI = ("", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ", "दस", "ग्यारह", "बारह")
MAX_RANGE_DAYS = 42

log = logging.getLogger("vakya.availability")
Intervals = List[Tuple[datetime, datetime]]


def calendar_source(schedule: dict) -> Optional[Callable[[date], Intervals]]:
    """The busy-interval reader of the owner's Google Calendar for this schedule, or None when no calendar is set up (then
    nothing here differs from a desk without Google)."""
    try:
        try:
            from services import gcal  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            from backend.services import gcal  # noqa: PLC0415
        if schedule.get("kind") == "nights" or not gcal.configured():
            return None
    except Exception:  # noqa: BLE001
        return None
    zone = schedule.get("timezone")
    return lambda d: gcal.day_busy(d, zone)


@dataclass
class Slot:
    start: datetime
    end: datetime
    status: str
    remaining: int
    bookings: List[dict] = field(default_factory=list)
    block_id: Optional[str] = None
    calendar: bool = False          # blocked by an event on the owner's Google Calendar, not by a Vakya block

    def public(self, tz: str, decorate=None) -> dict:
        """The slot as the API shows it. `decorate(booking)` (the desk's route) adds who the booking is for: the masked
        phone, where it came from and the caller's reference; the raw phone number never leaves this function."""
        out = {"start": sch.iso(self.start, tz), "end": sch.iso(self.end, tz), "status": self.status,
               "remaining": self.remaining,
               "bookings": [{"id": b["id"], "name": b["name"], **(decorate(b) if decorate else {})} for b in self.bookings],
               "block_id": self.block_id}
        if self.calendar:
            out["calendar"] = True
        return out


def live_bookings(rows: Iterable[dict], schedule_id: str) -> List[dict]:
    """The bookings of a schedule that still stand, as {id, name, start, end, nights} with naive datetimes. A row that
    cannot be read is skipped."""
    out = []
    for b in rows:
        if b.get("schedule_id") != schedule_id or b.get("status") == "cancelled":
            continue
        try:
            start = datetime.fromisoformat(str(b["start"]))
            end = datetime.fromisoformat(str(b["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"id": b.get("booking_id"), "name": b.get("name") or "", "start": start.replace(tzinfo=None),
                    "end": end.replace(tzinfo=None), "nights": b.get("nights"),
                    "phone": b.get("phone") or "", "source": b.get("source"), "conversation_id": b.get("conversation_id"),
                    "evidence_id": b.get("evidence_id")})
    return out


class Engine:
    def __init__(self, schedule: dict, bookings: Iterable[dict], blocks: Iterable[dict], now: datetime,
                 external: Optional[Callable[[date], Intervals]] = None):
        self.s = schedule
        self.external = external
        self._external_days: Dict[date, Intervals] = {}
        self._external_failed = False
        self.now = now
        self.today = now.date()
        self.tz = schedule.get("timezone")
        self.nights = schedule.get("kind") == "nights"
        self.slot = int(schedule["slot_minutes"])
        self.buffer = int(schedule.get("buffer_minutes") or 0)
        self.capacity = int(schedule["capacity"])
        self.week = sch.week_minutes(schedule["week"])
        self.closed = set(schedule.get("closed_dates") or [])
        self.min_notice = timedelta(minutes=int(schedule.get("min_notice_minutes") or 0))
        self.horizon_last = self.today + timedelta(days=int(schedule.get("horizon_days") or 30))
        self.bookings = list(bookings)
        self.blocks = [(datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]), b["id"]) for b in blocks]
        self.check_in = sch.to_min(schedule["check_in"]) if self.nights else 0
        self.check_out = sch.to_min(schedule["check_out"]) if self.nights else 0
        self._by_day: Dict[date, List[dict]] = {}
        for b in self.bookings:
            d = b["start"].date()
            while d <= b["end"].date():
                self._by_day.setdefault(d, []).append(b)
                d += timedelta(days=1)
        self._slots: Dict[date, List[Slot]] = {}

    # ------------------------------------------------------------------------------------------ the day

    def _calendar_busy(self, d: date) -> Intervals:
        """The owner's own events of a day (empty without a calendar, for a stay, for a day before today, and for the rest of
        this Engine's life after Google failed once: Vakya's own list then decides, which is what a desk without Google does)."""
        if self.external is None or self.nights or self._external_failed or d < self.today:
            return []
        if d not in self._external_days:
            try:
                self._external_days[d] = [(a.replace(tzinfo=None), b.replace(tzinfo=None)) for a, b in self.external(d)]
            except Exception as e:  # noqa: BLE001 - Google never fails a caller's request
                log.warning("calendar: could not read the owner's events (%s); using Vakya's own list", e)
                self._external_failed = True
                return []
        return self._external_days[d]

    def is_open(self, d: date) -> bool:
        return d.isoformat() not in self.closed and bool(self.week[d.weekday()])

    def _starts(self, d: date) -> List[datetime]:
        midnight = datetime.combine(d, datetime.min.time())
        if self.nights:
            return [midnight + timedelta(minutes=self.check_in)]
        out = []
        step = self.slot + self.buffer
        for a, b in self.week[d.weekday()]:
            t = a
            while t + self.slot <= b:
                out.append(midnight + timedelta(minutes=t))
                t += step
        return out

    def _span(self, start: datetime) -> datetime:
        if self.nights:
            return datetime.combine(start.date() + timedelta(days=1), datetime.min.time()) + timedelta(
                minutes=self.check_out)
        return start + timedelta(minutes=self.slot)

    def slots(self, d: date) -> List[Slot]:
        """Every slot of a date with its status (empty on a closed day)."""
        cached = self._slots.get(d)
        if cached is not None:
            return cached
        out: List[Slot] = []
        if self.is_open(d):
            day_bookings = self._by_day.get(d, [])
            busy = self._calendar_busy(d)
            for start in self._starts(d):
                end = self._span(start)
                mine = [b for b in day_bookings if b["start"] < end and b["end"] > start]
                block = next((bid for bs, be, bid in self.blocks if bs < end and be > start), None)
                on_calendar = block is None and start > self.now and any(bs < end and be > start for bs, be in busy)
                remaining = 0 if block or on_calendar else max(0, self.capacity - len(mine))
                if (d < self.today) if self.nights else (start <= self.now):
                    status = "past"
                elif block or on_calendar:
                    status = "blocked"
                elif len(mine) >= self.capacity:
                    status = "full"
                elif mine:
                    status = "booked"
                else:
                    status = "free"
                out.append(Slot(start, end, status, remaining, mine, block, calendar=on_calendar and status == "blocked"))
        self._slots[d] = out
        return out

    def day_public(self, d: date, decorate=None) -> dict:
        return {"date": d.isoformat(), "open": self.is_open(d), "slots": [s.public(self.tz, decorate) for s in self.slots(d)]}

    def range_public(self, a: date, b: date, decorate=None) -> dict:
        days, d = [], a
        while d <= b:
            days.append(self.day_public(d, decorate))
            d += timedelta(days=1)
        return {"timezone": self.tz or sch.timezone_name(), "kind": self.s["kind"], "days": days}

    # ------------------------------------------------------------------------------------------ what callers can book

    def caller_ok(self, s: Slot) -> bool:
        """A slot a caller may book now: free or part booked, with room, after the notice, within the horizon."""
        if s.status not in ("free", "booked") or s.remaining <= 0:
            return False
        if s.start.date() > self.horizon_last:
            return False
        return self.nights or s.start >= self.now + self.min_notice

    def bookable(self, d: date) -> List[Slot]:
        return [s for s in self.slots(d) if self.caller_ok(s)]

    def _stay_ok(self, d: date, nights: int) -> bool:
        for i in range(nights):
            day = d + timedelta(days=i)
            slots = self.slots(day)
            if not slots or not (slots[0].status in ("free", "booked") and slots[0].remaining > 0):
                return False
        return d >= self.today and d <= self.horizon_last

    def next_free(self, after: Optional[datetime] = None) -> Optional[dict]:
        """The first slot (or night) a caller could book from now, or after `after`."""
        if after is None and self.nights:
            for i in range(int(self.s.get("horizon_days") or 30) + 1):
                d = self.today + timedelta(days=i)
                if self._stay_ok(d, 1):
                    return self._stay(d, 1)
            return None
        return self._after(after or self.now, self.today, 1)

    # ------------------------------------------------------------------------------------------ nearest free

    def _after(self, moment: datetime, from_day: date, nights: int) -> Optional[dict]:
        """The first free slot starting after `moment` (a stay: the first check-in date after it), same day first."""
        d = max(from_day, self.today)
        while d <= self.horizon_last:
            if self.nights:
                if d > moment.date() and self._stay_ok(d, nights):
                    return self._stay(d, nights)
            else:
                for s in self.bookable(d):
                    if s.start > moment:
                        return self._pair(s)
            d += timedelta(days=1)
        return None

    def _before(self, moment: datetime, from_day: date, nights: int) -> Optional[dict]:
        """The last free slot starting before `moment` (a stay: the last earlier check-in date), same day first."""
        d = min(from_day, self.horizon_last)
        while d >= self.today:
            if self.nights:
                if d < moment.date() and self._stay_ok(d, nights):
                    return self._stay(d, nights)
            else:
                for s in reversed(self.bookable(d)):
                    if s.start < moment:
                        return self._pair(s)
            d -= timedelta(days=1)
        return None

    def _pair(self, s: Slot) -> dict:
        return {"start": sch.iso(s.start, self.tz), "end": sch.iso(s.end, self.tz)}

    def _stay(self, d: date, nights: int) -> dict:
        start = self.slots(d)[0].start
        end = self._span(start + timedelta(days=nights - 1))
        return {"start": sch.iso(start, self.tz), "end": sch.iso(end, self.tz)}

    # ------------------------------------------------------------------------------------------ check

    def check(self, *, day: Optional[date] = None, at: Optional[str] = None, nights: Optional[int] = None,
              party: Optional[int] = None, caller: bool = True) -> dict:
        """Is it free? `day` and `at` ("HH:MM") for slots, `day` (check-in) and `nights` for stays. Returns {available,
        requested, reason, before, after, spoken}."""
        if self.nights:
            res = self._check_stay(day, nights or 1, caller)
        else:
            res = self._check_slot(day, at, caller)
        en = speak(self, res, "en")
        res["spoken"] = {"en": en[:1].upper() + en[1:], "hi": speak(self, res, "hi")}
        res.pop("day", None)
        return res

    def _result(self, available: bool, reason: Optional[str], requested: Optional[dict], before=None, after=None,
                **extra) -> dict:
        out = {"available": available, "requested": requested, "reason": reason, "before": before, "after": after}
        out.update(extra)
        return out

    def _check_slot(self, day: Optional[date], at: Optional[str], caller: bool) -> dict:
        if day is None:
            return self._result(False, "unclear", None)
        if at is None:
            first = self._after(datetime.combine(day, datetime.min.time()) - timedelta(minutes=1), day, 1)
            return self._result(False, "unclear", None, None, first, day=day.isoformat())
        t = sch.to_min(at, "time")
        start = datetime.combine(day, datetime.min.time()) + timedelta(minutes=t)
        end = start + timedelta(minutes=self.slot)
        requested = {"start": sch.iso(start, self.tz), "end": sch.iso(end, self.tz)}

        def fail(reason: str) -> dict:
            return self._result(False, reason, requested, self._before(start, day, 1), self._after(start, day, 1))

        if start <= self.now:
            return fail("past")
        if caller and day > self.horizon_last:
            return fail("too_far")
        if not self.is_open(day):
            return fail("closed")
        slots = self.slots(day)
        hit = next((s for s in slots if s.start == start), None)
        if hit is None:
            inside = next((s for s in slots if s.start < start < s.end), None)
            if inside is None and any(s.start < start and s.end > start - timedelta(minutes=self.buffer) for s in slots):
                inside = True   # in the gap a buffer leaves after a slot
            return fail("not_a_start" if inside else "outside_hours")
        if caller and start < self.now + self.min_notice:
            return fail("too_soon")
        if hit.status == "blocked":
            return fail("calendar_busy" if hit.calendar else "blocked")
        if hit.remaining <= 0:
            return fail("full")
        return self._result(True, None, requested)

    def _check_stay(self, day: Optional[date], n: int, caller: bool) -> dict:
        n = max(1, min(int(n), sch.MAX_NIGHTS))
        if day is None:
            return self._result(False, "unclear", None, nights=n)
        start = datetime.combine(day, datetime.min.time()) + timedelta(minutes=self.check_in)
        end = self._span(start + timedelta(days=n - 1))
        requested = {"start": sch.iso(start, self.tz), "end": sch.iso(end, self.tz)}

        def fail(reason: str) -> dict:
            return self._result(False, reason, requested, self._before(start, day, n), self._after(start, day, n),
                                nights=n)

        if day < self.today:
            return fail("past")
        if caller and day > self.horizon_last:
            return fail("too_far")
        reason = None
        for i in range(n):
            slots = self.slots(day + timedelta(days=i))
            if not slots:
                return fail("closed")
            if slots[0].status == "blocked":
                reason = reason or "blocked"
            elif slots[0].remaining <= 0:
                reason = reason or "full"
        if reason:
            return fail(reason)
        return self._result(True, None, requested, nights=n)

    # ------------------------------------------------------------------------------------------ summary for the card

    def summary(self) -> dict:
        today = self.slots(self.today)
        if self.nights:
            free = sum(s.remaining for s in today)
            booked = sum(len(s.bookings) for s in today)
        else:
            free = sum(1 for s in today if s.status in ("free", "booked") and s.remaining > 0)
            booked = sum(len(s.bookings) for s in today)
        nxt = self.next_free()
        return {"today_free": free, "today_booked": booked, "next_free": nxt["start"] if nxt else None,
                "hours_text": sch.hours_text(self.s)}

    def free_times(self, d: date) -> List[str]:
        """The start times ("HH:MM") a caller could book on a date."""
        return [s.start.strftime("%H:%M") for s in self.bookable(d)]


# ---------------------------------------------------------------------------------------------- spoken answers

def _hour12(h: int) -> int:
    return h % 12 or 12


def time_en(dt: datetime) -> str:
    if dt.hour == 12 and dt.minute == 0:
        return "noon"
    if dt.hour == 0 and dt.minute == 0:
        return "midnight"
    suffix = "am" if dt.hour < 12 else "pm"
    return f"{_hour12(dt.hour)}:{dt.minute:02d} {suffix}" if dt.minute else f"{_hour12(dt.hour)} {suffix}"


def period_hi(hour: int) -> str:
    return "सुबह" if 4 <= hour < 12 else "दोपहर" if 12 <= hour < 16 else "शाम" if 16 <= hour < 20 else "रात"


def time_hi(dt: datetime, with_period: bool = True) -> str:
    """"शाम छह बजे", "साढ़े पाँच बजे", "सवा दस बजे", "पौने सात बजे", "डेढ़ बजे", "ढाई बजे"."""
    h12, m = _hour12(dt.hour), dt.minute
    if m == 0:
        base = f"{HOURS_HI[h12]} बजे"
    elif m == 15:
        base = f"सवा {HOURS_HI[h12]} बजे"
    elif m == 30:
        base = "डेढ़ बजे" if h12 == 1 else "ढाई बजे" if h12 == 2 else f"साढ़े {HOURS_HI[h12]} बजे"
    elif m == 45:
        base = f"पौने {HOURS_HI[h12 % 12 + 1]} बजे"
    else:
        base = f"{HOURS_HI[h12]} बजकर {m} मिनट"
    return f"{period_hi(dt.hour)} {base}" if with_period else base


def day_en(d: date, today: date) -> str:
    delta = (d - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if 0 < delta < 7:
        return WEEKDAYS_EN[d.weekday()]
    return f"{WEEKDAYS_EN[d.weekday()]} {d.day} {MONTHS_EN[d.month - 1]}"


def day_hi(d: date, today: date) -> str:
    delta = (d - today).days
    if delta == 0:
        return "आज"
    if delta == 1:
        return "कल"
    if 0 < delta < 7:
        return WEEKDAYS_HI[d.weekday()]
    return f"{d.day} {MONTHS_HI[d.month - 1]} ({WEEKDAYS_HI[d.weekday()]})"


def _when(dt: datetime, today: date, lang: str, *, day: bool = True, period: bool = True) -> str:
    if lang == "hi":
        return " ".join(x for x in (day_hi(dt.date(), today) if day else "", time_hi(dt, period)) if x)
    return f"{day_en(dt.date(), today)} at {time_en(dt)}" if day else time_en(dt)


def _dt(value: Optional[dict]) -> Optional[datetime]:
    return datetime.fromisoformat(value["start"]).replace(tzinfo=None) if value else None


def _span_text(minutes: int, lang: str) -> str:
    if lang == "hi":
        return f"{minutes} मिनट" if minutes < 60 else (f"{minutes // 60} घंटे" if minutes % 60 == 0 else f"{minutes} मिनट")
    if minutes < 60:
        return f"{minutes} minutes"
    return f"{minutes // 60} hour{'s' if minutes // 60 != 1 else ''}" if minutes % 60 == 0 else f"{minutes} minutes"


def _options(res: dict, today: date, lang: str, ref: Optional[datetime], nights: Optional[int] = None) -> str:
    """"5:30 pm or 6:30 pm, which would suit you?" for the nearest free times either side."""
    before, after = _dt(res.get("before")), _dt(res.get("after"))
    if nights:
        def stay(dt: datetime) -> str:
            return (f"{day_hi(dt.date(), today)} से {nights} रात" if lang == "hi"
                    else f"{day_en(dt.date(), today)} for {nights} night{'s' if nights != 1 else ''}")
        parts = [stay(x) for x in (before, after) if x]
    else:
        def one(dt: datetime) -> str:
            same_day = ref is not None and dt.date() == ref.date()
            same_period = same_day and ref is not None and period_hi(dt.hour) == period_hi(ref.hour)
            return _when(dt, today, lang, day=not same_day, period=not same_period)
        parts = [one(x) for x in (before, after) if x]
    if len(parts) == 2:
        # a sentence of its own: "Today at 7:45 pm or 10:45 am, which would suit you?"
        return (f"{parts[0]} या {parts[1]}, कौन सा ठीक रहेगा?" if lang == "hi"
                else f"{parts[0][:1].upper()}{parts[0][1:]} or {parts[1]}, which would suit you?")
    if len(parts) == 1:
        return (f"सबसे पास का खाली समय {parts[0]} है। क्या वह ठीक रहेगा?" if lang == "hi"
                else f"The nearest free time is {parts[0]}. Would that work?")
    return ""


def speak(engine: Engine, res: dict, lang: str) -> str:
    """The answer in a sentence or two the agent can say as it is. `lang` is "en" or "hi"."""
    today = engine.today
    hi = lang == "hi"
    reason = res.get("reason")
    req = _dt(res.get("requested"))
    n = res.get("nights") if engine.nights else None
    horizon = int(engine.s.get("horizon_days") or 30)
    if reason == "unclear":
        if engine.nights:
            return "किस तारीख से और कितनी रात के लिए?" if hi else "Which check-in date, and for how many nights?"
        if res.get("day"):
            d = date.fromisoformat(res["day"])
            first = _dt(res.get("after"))
            ask = f"{day_hi(d, today)} किस समय चाहिए?" if hi else f"What time on {day_en(d, today)}?"
            if first is not None and first.date() == d:
                return ask + (f" सबसे पहला खाली समय {time_hi(first)} है।" if hi
                              else f" The first free time is {time_en(first)}.")
            return ask
        return "कौन सा दिन और समय चाहिए?" if hi else "Which day and time would you like?"
    if req is None:
        return ""
    if reason == "not_a_start":   # a time inside the hours that is not where a slot starts: say the step, offer the neighbours
        step = engine.slot + engine.buffer
        return ((f"हम हर {step} मिनट पर बुकिंग करते हैं। " if hi else f"We book every {step} minutes. ")
                + _options(res, today, lang, req))
    if engine.nights:
        d_end = _dt({"start": res["requested"]["end"]})
        stay_hi = f"{day_hi(req.date(), today)} से {n} रात" if n else day_hi(req.date(), today)
        stay_en = f"{n} night{'s' if n != 1 else ''} from {day_en(req.date(), today)}"
        if res["available"]:
            return (f"{stay_hi} के लिए कमरा उपलब्ध है।" if hi else f"A room is available for {stay_en}.") + ""
        first = {"past": ("वह तारीख निकल चुकी है।", "That date has already passed."),
                 "too_far": (f"हम {horizon} दिन आगे तक की ही बुकिंग लेते हैं।",
                             f"We take bookings up to {horizon} days ahead."),
                 "closed": (f"{stay_hi} के लिए हम बुकिंग नहीं ले सकते।", f"We cannot take {stay_en}."),
                 "blocked": (f"{stay_hi} के लिए कमरे उपलब्ध नहीं हैं।", f"Rooms are not available for {stay_en}."),
                 "full": (f"{stay_hi} के लिए सभी कमरे भरे हुए हैं।", f"We are fully booked for {stay_en}.")}[reason]
        opts = _options(res, today, lang, req, n or 1)
        return f"{first[0] if hi else first[1]} {opts or (f'अगले {horizon} दिनों में खाली कमरा नहीं दिख रहा।' if hi else f'I do not see a free room in the next {horizon} days.')}".strip()
    when_hi = _when(req, today, "hi")
    when_en = _when(req, today, "en")
    opts = _options(res, today, lang, req)
    none_found = (f"माफ़ कीजिए, अगले {horizon} दिनों में कोई खाली समय नहीं दिख रहा।" if hi
                  else f"Sorry, I do not see any free time in the next {horizon} days.")
    tail = opts or none_found
    if res["available"]:
        return f"{when_hi} का समय खाली है।" if hi else f"{when_en} is free."
    if reason == "full":
        return f"{when_hi} वाला समय भरा हुआ है। {tail}" if hi else f"{when_en} is already taken. {tail}"
    if reason == "blocked":
        return f"{when_hi} हम उपलब्ध नहीं हैं। {tail}" if hi else f"We are not available {when_en}. {tail}"
    if reason == "calendar_busy":   # something else is in the owner's own calendar then
        return f"{when_hi} हम व्यस्त हैं। {tail}" if hi else f"We are busy {when_en}. {tail}"
    if reason == "closed":
        day_word = day_hi(req.date(), today)
        return f"{day_word} को हम बंद रहते हैं। {tail}" if hi \
            else f"We are closed {'on ' if day_en(req.date(), today) not in ('today', 'tomorrow') else ''}" \
                 f"{day_en(req.date(), today)}. {tail}"
    if reason == "outside_hours":
        return f"{when_hi} हमारे खुलने के समय में नहीं है। {tail}" if hi \
            else f"{when_en} is outside our opening hours. {tail}"
    if reason == "past":
        return f"वह समय निकल चुका है। {tail}" if hi else f"That time has already passed. {tail}"
    if reason == "too_soon":
        span = _span_text(int(engine.s.get("min_notice_minutes") or 0), lang)
        return (f"इतनी जल्दी बुकिंग नहीं हो सकती, कम से कम {span} पहले बताना होगा। {tail}" if hi
                else f"That is too soon, we need at least {span} notice. {tail}")
    if reason == "too_far":
        return (f"हम {horizon} दिन आगे तक की ही बुकिंग लेते हैं। {tail}" if hi
                else f"We take bookings up to {horizon} days ahead. {tail}")
    return ""
