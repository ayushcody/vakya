"""Opening hours from words: "Mon to Sat 10 to 1 and 5 to 9, Sunday closed", "सोमवार से शनिवार सुबह 10 से 2".

`parse(text)` returns {week, slot_minutes, closed_dates, notes, confident, source}. A deterministic parser reads the text
first (microseconds, no model). Only when it understood nothing at all is the language model asked (services/llm.py,
whatever provider is configured), and its answer goes through the same validation as a week sent to the API
(services/schedules.normalise_week). `source` says which one answered: "parser", "model" or None.

What the parser reads
  * days: names in English, romanised Hindi and Marathi and Devanagari (Mon, Monday, somvaar, सोमवार, मंगळवार); ranges
    ("Mon to Sat", "Mon-Fri", "सोमवार से शनिवार", "सोमवार ते शुक्रवार", a range may wrap: "Fri to Mon"); lists ("Mon, Wed
    and Fri"); "all days / every day / daily / roz / रोज़", "weekdays", "weekends"; "except Sunday";
  * times: "10 to 1", "10am-6pm", "10:30 to 14:00", "9 se 5", "10 बजे से 2 बजे तक", "सुबह 10 से 2", "सकाळी 10 ते 2",
    number words (das se do, दस से दो), "साढ़े नौ", "open 24 hours", "24x7";
  * "closed", "off", "बंद", "छुट्टी", "सुट्टी" for a day, with the day before or after the word; "break" or "lunch" (also
    "ब्रेक", "लंच") for a gap, before or after its range, in the day group it follows or in every day;
  * the slot length ("every 15 minutes", "30 min slots", "hourly", "हर 20 मिनट") and closed dates ("closed 25 December",
    "15 Nov off");
  * a time with no am or pm: the earliest reading that makes the range end after it starts and is shortest, and when
    that is a guess (5 to 9 could be morning or evening) a note says so and `confident` is false. 1 to 7 are taken as
    afternoon or evening, 8 to 11 as morning, like services/when.py.

What it does not read: ranges that cross midnight ("10 pm to 2 am": split it into two days), different hours on
alternating weeks, dates of the month ("first Sunday"), hours for a named holiday ("Diwali"), languages other than
English, Hindi and Marathi. Words it does not recognise are listed in `notes` and make `confident` false.

Everything is lowercased and normalised with services/when.py's cleaner first."""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Dict, List, Optional, Tuple

try:
    from services import schedules as sch, when
except ImportError:  # pragma: no cover
    from backend.services import schedules as sch, when

log = logging.getLogger("vakya.hours")

# ---------------------------------------------------------------------------------------------- vocabulary

_B, _E = when._B, when._E

_DAY_WORDS = dict(when.WEEKDAY_WORDS)
for _w in ("sundays", "mondays"):
    _DAY_WORDS.pop(_w, None)
_DAY_ALT = "|".join(sorted(map(re.escape, _DAY_WORDS), key=len, reverse=True))
_DAY = _B + r"(?:" + _DAY_ALT + r")s?" + _E
_SEP = r"(?:to|till|until|through|thru|-|–|se|से|ते|tak|तक|पासून)"

_ALL_DAYS = (r"(?:all days|every day|everyday|daily|all week|seven days|7 days(?: a week)?|all 7 days|rozana|roz|रोज़|रोज|"
             r"रोजाना|हर दिन|सातों दिन|सभी दिन|सप्ताह के सभी दिन|हररोज|दररोज)")
_WEEKDAYS = r"(?:weekdays|week days|working days|कार्यदिवस|कामकाजी दिन)"
_WEEKENDS = r"(?:weekends?|week end|सप्ताहांत)"
_OPEN24 = (r"(?:open\s+)?(?:24\s*(?:hours|hrs|hr|x\s*7|/\s*7)|24 घंटे|चौबीस घंटे|round the clock|around the clock)"
           r"(?:\s+(?:a day|open|daily))?")
_CLOSED = (r"(?:closed|off|holidays?|shut|no appointments|not open|band|बंद|छुट्टी|सुट्टी|chhutti|suttee|"
           r"नहीं खुलता|नहीं खुलते|खुला नहीं)")
_BREAK = r"(?:break|lunch|interval|brk|ब्रेक|लंच|भोजन|विश्राम|जेवण|खाने की छुट्टी)"
_EXCEPT = r"(?:except|excluding|but not|छोड़कर|छोड़ के|chhodkar|chhod ke|वगळता)"

_PERIOD = (r"(?:subah|subha|savere|morning|सुबह|सवेरे|सकाळी|sakali|dopahar|dopaher|afternoon|दोपहर|दुपारी|dupari|shaam|"
           r"sham|shyam|evening|शाम|संध्याकाळी|सायंकाळी|raat|night|रात्री|रात|ratri)")
_MORNING = {"subah", "subha", "savere", "morning", "सुबह", "सवेरे", "सकाळी", "sakali"}
_NUM_WORDS = {k: v for k, v in when.NUMBER_WORDS.items() if k not in ("sat",) and 1 <= v <= 12}
_NUM_ALT = "|".join(sorted(map(re.escape, _NUM_WORDS), key=len, reverse=True))
_FRAC = r"(?:saade|sadhe|saadhe|sade|साढ़े|साढे|साडे|sawa|sava|सवा|सव्वा|paune|pone|पौने|पावणे)"
_FRACS = {"saade": 30, "sadhe": 30, "saadhe": 30, "sade": 30, "साढ़े": 30, "साढे": 30, "साडे": 30, "sawa": 15, "sava": 15,
          "सवा": 15, "सव्वा": 15, "paune": -15, "pone": -15, "पौने": -15, "पावणे": -15}
_FIXED = {"dedh": (1, 30), "डेढ़": (1, 30), "डेढ": (1, 30), "dhai": (2, 30), "ढाई": (2, 30)}


def _tp(p: str) -> str:
    """One time of day as a regex with named groups p+pre (a period word), frac, num, ap (am/pm), post."""
    return (rf"(?:(?P<{p}pre>{_B}{_PERIOD}{_E})\s*)?"
            rf"(?:(?P<{p}frac>{_B}{_FRAC}{_E})\s*)?"
            rf"(?P<{p}num>\d{{1,2}}(?::\d{{2}})?|{_B}(?:{_NUM_ALT}|dedh|dhai|डेढ़|डेढ|ढाई){_E})"
            rf"\s*(?:(?P<{p}ap>am|pm){_E}|{_B}(?:baje|बजे|वाजता|वाजे|oclock|hrs|hr){_E})?"
            rf"(?:\s+(?P<{p}post>{_B}{_PERIOD}{_E}))?")


_MONTH_ALT = "|".join(sorted(map(re.escape, when.MONTH_WORDS), key=len, reverse=True))
_DATE = (r"(?:\d{4}-\d{1,2}-\d{1,2}|" + _B + r"\d{1,2}(?:st|nd|rd|th)?\s+(?:" + _MONTH_ALT + r")" + _E + r"(?:\s+\d{4})?|" +
         _B + r"(?:" + _MONTH_ALT + r")\s+\d{1,2}(?:st|nd|rd|th)?" + _E + r"(?:\s+\d{4})?|\d{1,2}/\d{1,2}(?:/\d{2,4})?)")

_MASTER = re.compile("|".join([
    rf"(?P<open24>{_B}{_OPEN24}{_E})",
    rf"(?P<date>{_DATE})",
    rf"(?P<slot>{_B}(?:(?P<slotn>\d{{1,3}})\s*(?:min|mins|minute|minutes|मिनट|मिनिटे|मिनिट){_E}|"
    rf"(?P<hourly>hourly|every hour|हर घंटे|दर तासाला)|(?P<half>every half hour|हर आधे घंटे)))",
    rf"(?P<between>{_B}between\s+{_tp('c')}\s*(?:and|aur|और)\s*{_tp('d')}\s*(?:{_B}(?:tak|तक){_E})?)",
    rf"(?P<drange>(?P<d1>{_DAY})\s*{_SEP}\s*(?P<d2>{_DAY}))",
    rf"(?P<alldays>{_B}{_ALL_DAYS}{_E})",
    rf"(?P<weekdays>{_B}{_WEEKDAYS}{_E})",
    rf"(?P<weekends>{_B}{_WEEKENDS}{_E})",
    rf"(?P<day>{_DAY})",
    rf"(?P<trange>{_tp('a')}\s*{_SEP}\s*{_tp('b')}\s*(?:{_B}(?:tak|तक){_E})?)",
    rf"(?P<closed>{_B}{_CLOSED}{_E})",
    rf"(?P<brk>{_B}{_BREAK}{_E})",
    rf"(?P<exc>{_B}{_EXCEPT}{_E})",
]), re.IGNORECASE)

_STOP = set("""open opens opening hours hour time timing timings from on at is are the a an and aur of for we our clinic office
shop store between with ko me mein में को पर और या की का के है हैं हूँ हम रहता रहती रहते खुला खुलता खुलते in all only also
please from till until then to se tak ते से तक days day slots slot each per every sorry hi hello ok okay और आणि आहे आहेत सर्व दिवस दिन the""".split())

# ---------------------------------------------------------------------------------------------- times of a range

Endpoint = Tuple[List[int], bool]   # candidate minutes, explicit (am/pm, a period word or a 24-hour time was given)


def _endpoint(g: Dict[str, Optional[str]], p: str) -> Endpoint:
    num = (g.get(p + "num") or "").lower()
    frac = (g.get(p + "frac") or "").lower()
    ap = (g.get(p + "ap") or "").lower()
    period = (g.get(p + "pre") or g.get(p + "post") or "").lower()
    if num in _FIXED:
        h, m = _FIXED[num]
    elif ":" in num:
        h, m = (int(x) for x in num.split(":"))
    else:
        h, m = (int(num) if num.isdigit() else _NUM_WORDS.get(num, 0)), 0
    if frac:
        delta = _FRACS.get(frac, 0)
        if delta < 0:
            h, m = (h - 1 if h > 1 else 12), 45
        else:
            m = delta
    if h > 24 or m > 59:
        return [], False
    if h > 12 or (h == 0) or (num.startswith("0") and len(num) > 1):
        return [h * 60 + m], True            # a 24-hour time
    if ap:
        h = (0 if h == 12 else h) + (12 if ap == "pm" else 0)
        return [h * 60 + m], True
    if period:
        if period in _MORNING:
            return [(0 if h == 12 else h) * 60 + m], True
        return [(h if h == 12 else h + 12) * 60 + m], True
    if h == 12:
        return [12 * 60 + m, 24 * 60 + m if m == 0 else 12 * 60 + m], False
    return [h * 60 + m, (h + 12) * 60 + m], False


def _range(a: Endpoint, b: Endpoint) -> Optional[Tuple[int, int, bool]]:
    """(start, end, guessed): the shortest reading in which the range ends after it starts; ties go to the reading with the
    start where services/when.py puts it (1 to 7 afternoon, 8 to 11 morning)."""
    (sa, ea), (sb, eb) = a, b
    if not sa or not sb:
        return None

    def prior(c: List[int]) -> List[int]:
        first = c[0] // 60
        return [c[1], c[0]] if 1 <= first <= 7 and len(c) == 2 else c

    best = None
    for rank, s in enumerate(prior(sa) if not ea else sa):
        for e in sorted(set(sb)):
            if e > s and e - s <= 18 * 60:
                key = (e - s, rank)
                if best is None or key < best[0]:
                    best = (key, s, e)
    if best is None:
        return None
    guessed = (not ea or not eb) and best[1] // 60 in (6, 7, 18, 19) and any(
        e2 > s2 and e2 - s2 == best[0][0] and (s2, e2) != (best[1], best[2]) for s2 in sa for e2 in sb)
    return best[1], best[2], guessed


# ---------------------------------------------------------------------------------------------- the parser

class _Group:
    def __init__(self, days: Optional[set] = None):
        self.days = set(days or ())
        self.ranges: List[Tuple[int, int]] = []
        self.breaks: List[Tuple[int, int]] = []
        self.closed = False
        self.excluding = False
        self.default_days = False
        self.prefix_closed = False      # made by "closed Sunday and Monday": a list that may go on

    @property
    def payload(self) -> bool:
        return bool(self.ranges or self.breaks or self.closed)


_DAYISH = ("day", "drange", "alldays", "weekdays", "weekends")


def _day_num(word: str) -> int:
    return _DAY_WORDS[word] if word in _DAY_WORDS else _DAY_WORDS[word[:-1]]


def _days_between(a: int, b: int) -> List[int]:
    out, d = [a], a
    while d != b:
        d = (d + 1) % 7
        out.append(d)
    return out


def _merge(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    out: List[List[int]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _subtract(ranges: List[Tuple[int, int]], cuts: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    for ca, cb in cuts:
        nxt = []
        for a, b in ranges:
            if cb <= a or ca >= b:
                nxt.append((a, b))
                continue
            if a < ca:
                nxt.append((a, ca))
            if cb < b:
                nxt.append((cb, b))
        ranges = nxt
    return ranges


def _date_of(text: str, today: date) -> Optional[date]:
    d = when._explicit_date(when._clean(text), today)   # noqa: SLF001 - one reading of dates for the whole app
    return d


def parse_rules(text: str, today: Optional[date] = None) -> dict:
    """The deterministic parser. Returns the result dict (without `source`)."""
    today = today or sch.now().date()
    clean = when._clean(text)   # noqa: SLF001
    clean = clean.replace("–", "-").replace("—", "-")
    notes: List[str] = []
    unread: List[str] = []
    slot_minutes: Optional[int] = None
    closed_dates: List[str] = []
    groups: List[_Group] = []
    global_breaks: List[Tuple[int, int]] = []
    current: Optional[_Group] = None
    pending_closed = False
    guessed_notes: List[str] = []

    # tokens, with the text between them for the "did not understand" list
    tokens: List[Tuple[str, re.Match]] = []
    gaps: List[str] = []
    last = 0
    for m in _MASTER.finditer(clean):
        gap = clean[last:m.start()]
        for w in re.findall(r"[\w" + "ऀ-ॿ" + r"']+", gap):
            if w not in _STOP and not w.isdigit() and len(w) > 1:
                unread.append(w)
        kind = next(k for k in ("open24", "date", "slot", "between", "drange", "alldays", "weekdays", "weekends", "day",
                                "trange", "closed", "brk", "exc") if m.group(k))
        tokens.append((kind, m))
        gaps.append(gap)
        last = m.end()
    for w in re.findall(r"[\w" + "ऀ-ॿ" + r"']+", clean[last:]):
        if w not in _STOP and not w.isdigit() and len(w) > 1:
            unread.append(w)

    def new_group(days: set) -> _Group:
        nonlocal current, pending_closed
        g = _Group(days)
        if pending_closed:
            g.closed, g.prefix_closed, pending_closed = True, True, False
        groups.append(g)
        current = g
        return g

    def add_days(days: List[int], at: int) -> None:
        nonlocal current
        joined = re.search(_B + r"(?:and|aur|और|&|या|आणि)" + _E, gaps[at]) is not None
        if current is not None and current.excluding:
            current.days -= set(days)
            return
        if current is not None and current.prefix_closed and joined and at and tokens[at - 1][0] in _DAYISH:
            current.days |= set(days)          # "closed on Sundays and Mondays"
            return
        nxt = tokens[at + 1][0] if at + 1 < len(tokens) else None
        if current is not None and current.default_days and current.ranges and not current.closed and nxt != "closed":
            current.days, current.default_days = set(days), False      # "10 to 6 on weekdays": the days come after the hours
            return
        if current is None or current.payload:
            new_group(set(days))
        else:
            current.days |= set(days)

    def time_range(m: re.Match, p1: str, p2: str) -> Optional[Tuple[int, int]]:
        g = m.groupdict()
        a, b = _endpoint(g, p1), _endpoint(g, p2)
        got = _range(a, b)
        if got is None:
            notes.append(f"Could not read the times '{m.group(0).strip()}'.")
            unread.append(m.group(0).strip())
            return None
        s, e, guessed = got
        if guessed:
            guessed_notes.append(f"Read '{m.group(0).strip()}' as {sch.hhmm(s)} to {sch.hhmm(e)}; say am or pm if you "
                                 "meant the other half of the day.")
        return s, e

    i = 0
    while i < len(tokens):
        kind, m = tokens[i]
        if kind == "slot":
            if m.group("slotn"):
                n = int(m.group("slotn"))
                if 5 <= n <= 240:
                    slot_minutes = n
                else:
                    notes.append(f"A slot of {n} minutes is not possible (5 to 240).")
            elif m.group("hourly"):
                slot_minutes = 60
            else:
                slot_minutes = 30
        elif kind == "date":
            d = _date_of(m.group(0), today)
            if d is not None:
                closed_dates.append(d.isoformat())
            else:
                unread.append(m.group(0))
        elif kind == "open24":
            if current is None:
                new_group(set(range(7)))
                current.default_days = True
            current.ranges.append((0, 1440))
        elif kind == "drange":
            add_days(_days_between(_day_num(m.group("d1")), _day_num(m.group("d2"))), i)
        elif kind in ("alldays", "weekdays", "weekends"):
            add_days(list(range(7)) if kind == "alldays" else list(range(5)) if kind == "weekdays" else [5, 6], i)
        elif kind == "day":
            add_days([_day_num(m.group("day"))], i)
        elif kind == "exc":
            if current is None:
                new_group(set(range(7)))
                current.default_days = True
            current.excluding = True
        elif kind == "closed":
            if current is not None and not current.payload and current.days and not current.excluding:
                current.closed = True
            elif current is not None and current.excluding:
                current.closed = False          # "all days except Sunday closed": the exclusion stays an exclusion
            else:
                pending_closed = True
        elif kind == "brk":
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            prev = tokens[i - 1] if i else None
            got = None
            if nxt and nxt[0] == "brk":                           # "lunch break 1 to 2": the second word counts
                i += 1
                continue
            if nxt and nxt[0] in ("trange", "between"):          # "lunch 1 to 2"
                got = time_range(nxt[1], *(("c", "d") if nxt[0] == "between" else ("a", "b")))
                i += 1
            elif prev and prev[0] in ("trange", "between") and current is not None and current.ranges:
                got = time_range(prev[1], *(("c", "d") if prev[0] == "between" else ("a", "b")))   # "1 to 2 break"
                if got in current.ranges:
                    current.ranges.remove(got)
            if got:
                if current is not None and not current.closed:
                    current.breaks.append(got)
                else:
                    global_breaks.append(got)
        elif kind in ("trange", "between"):
            got = time_range(m, *(("c", "d") if kind == "between" else ("a", "b")))
            if got:
                if current is None:
                    new_group(set(range(7)))
                    current.default_days = True
                elif current.payload and current.closed:
                    pass
                if current.excluding:
                    current.excluding = False
                current.ranges.append(got)
        i += 1

    week_sets: Dict[int, List[Tuple[int, int]]] = {}
    for g in groups:
        days = sorted(g.days)
        if not days and g.default_days:
            days = list(range(7))
        if g.closed:
            for d in days:
                week_sets[d] = []
            continue
        if not g.ranges:
            if days and not g.breaks:
                names = ", ".join(sch.DAY_NAMES[d] for d in days)
                notes.append(f"No hours were given for {names}.")
            continue
        merged = _merge(g.ranges)
        if len(merged) != len(g.ranges):
            notes.append("Some of the ranges overlapped, so they were joined.")
        cut = _merge(g.breaks + global_breaks)
        final = _subtract(merged, cut)
        for d in days:
            week_sets[d] = list(final)
    week = {sch.DAYS[d]: [{"start": sch.hhmm(a), "end": sch.hhmm(b)} for a, b in week_sets.get(d, []) if b > a]
            for d in range(7)}
    understood = bool(week_sets) or bool(closed_dates) or slot_minutes is not None
    notes += guessed_notes
    if unread:
        notes.append("I did not understand: " + ", ".join(dict.fromkeys(unread)) + ".")
    confident = understood and not unread and not guessed_notes and not any(n.startswith(("No hours", "Could not")) for n in notes)
    return {"week": week, "slot_minutes": slot_minutes, "closed_dates": sorted(set(closed_dates)), "notes": notes,
            "confident": bool(confident), "understood_hours": bool(week_sets)}


# ---------------------------------------------------------------------------------------------- the model fallback

_PROMPT = ("Read the opening hours below and answer ONLY with JSON: {\"week\": {\"mon\": [{\"start\": \"10:00\", \"end\": "
           "\"13:00\"}], \"tue\": [], \"wed\": [], \"thu\": [], \"fri\": [], \"sat\": [], \"sun\": []}, \"slot_minutes\": "
           "null, \"closed_dates\": []}. Times are 24-hour HH:MM; a closed day is []; a break is a gap between two ranges; "
           "slot_minutes is the length of one appointment if the text says it, else null; closed_dates are YYYY-MM-DD. "
           "The text may be English, Hindi or Marathi.")


def _ask_model(text: str) -> Optional[dict]:
    """The week as the language model reads it, validated; None when no model answers or the answer is unusable."""
    try:
        from services import llm  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.services import llm  # noqa: PLC0415
    try:
        res = llm.chat([{"role": "system", "content": _PROMPT}, {"role": "user", "content": text}], json_mode=True,
                       temperature=0.0, max_tokens=600, timeout=20.0)
        data = llm.extract_json(res.text)
        week = sch.normalise_week(data.get("week"))
        slot = data.get("slot_minutes")
        slot = int(slot) if isinstance(slot, (int, float)) and not isinstance(slot, bool) and 5 <= int(slot) <= 240 else None
        dates = []
        for d in data.get("closed_dates") or []:
            try:
                dates.append(sch.parse_day(d, "closed_dates").isoformat())
            except sch.ScheduleError:
                continue
        if not any(week.values()) and not dates:
            return None
        return {"week": week, "slot_minutes": slot, "closed_dates": sorted(set(dates))}
    except Exception as e:  # noqa: BLE001 - no model, a bad answer or an invalid week: the parser's answer stands
        log.info("hours: the language model did not help (%r)", e)
        return None


def parse(text: str, today: Optional[date] = None, use_model: bool = True) -> dict:
    """Opening hours from words: see the module docstring. Never raises."""
    result = parse_rules(text, today)
    understood = result.pop("understood_hours")
    if not understood and result["slot_minutes"] is None and not result["closed_dates"] and use_model:
        asked = _ask_model(text)
        if asked is not None:
            return {**asked, "notes": ["Read by the language model, please check the hours."], "confident": False,
                    "source": "model"}
    try:   # the week is always valid: the same check as a week sent to the API
        result["week"] = sch.normalise_week(result["week"])
    except sch.ScheduleError as e:
        result["week"] = {d: [] for d in sch.DAYS}
        result["notes"].append(e.message)
        result["confident"] = False
    if not understood:
        result["confident"] = False
        if not result["notes"]:
            result["notes"].append("I could not read any opening hours from that.")
    return {**result, "source": "parser" if understood or result["slot_minutes"] or result["closed_dates"] else None}
