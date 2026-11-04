"""Dates, times and slots from what a caller says, worked out in code, never by the language model.

Why: the model was given a clock line and asked to compute "next Monday" or "parso" itself, and on 21 Oct 2026 the
fallback model turned a Monday into a Saturday. Here the model passes the caller's words through to the tool as they
were said ("kal", "agle somvaar", "5 baje", "next Monday 11 am") and `normalise_tool_args` turns them into the
YYYY-MM-DD and HH:MM the appointment and meeting tools expect. Deterministic rules, microseconds, no model call.

Covers English, romanised Hindi/Hinglish and Devanagari:
  * relative days: today/aaj/आज, tomorrow/kal/कल (in a booking "kal" is tomorrow), day after tomorrow/parso/परसों,
    narso (three days on), "in 3 days" / "3 din baad", next week / agle hafte;
  * weekdays in all three scripts, with or without "next"/"agle"/"अगले": the next such day after today; a bare weekday
    may be today when the time asked for has not passed yet;
  * explicit dates: 27 October, 27th Oct, October 27, 27/10, 27-10-2026, 27 अक्टूबर, YYYY-MM-DD (passed through);
    a month-day that has already passed this year means next year;
  * times: 11 am, 11:30 pm, 11.30, 14:30 (passed through), 5 baje, paanch baje, saade paanch (5:30), dhai (2:30),
    dedh (1:30), sawa (quarter past), paune (quarter to), half past ten, quarter to five, ten thirty, noon, ५ बजे.
    Hours without am/pm or a time of day: 8-11 are morning, 12 is noon, 1-7 are afternoon or evening (a clinic or an
    office), and `assumed_meridiem` is set so a read-back can ask. subah/savere/सुबह/morning make it AM; dopahar/sham/
    raat/दोपहर/शाम/रात/afternoon/evening/night make it PM.

Also (see tests/test_when_hindi.py): साढ़े / सवा / पौने / डेढ़ / ढाई and the Marathi साडे / सव्वा / पावणे / दीड / अडीच (and romanised
forms) read as hour plus minutes ("साढ़े चार बजे" is 4:30, never 3:30); am/pm come from a time-of-day word, else from the
schedule's opening hours (`opening`, see `opening_ranges`) when only one reading falls inside them, else from the rule above;
a run of five or more spoken digits (a phone number), a party size ("चार लोग", "for four people") and a number word on its
own ("ek") are not times. `time_mentions` lists the times a text names, for the read-back check (services/readback.py).

Everything is in the machine's local time, like the MCP servers (mcp_servers/appointments.py, meetings.py)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")

# A token boundary that also works for Devanagari (Python's \b does not treat vowel signs as word characters).
_B = r"(?<![\wऀ-ॿ])"
_E = r"(?![\wऀ-ॿ])"

WEEKDAY_WORDS: Dict[str, int] = {
    # English
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3,
    "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
    # Romanised Hindi / Marathi
    "somvaar": 0, "somvar": 0, "somwar": 0, "somwaar": 0,
    "mangalvaar": 1, "mangalvar": 1, "mangalwar": 1, "mangalwaar": 1,
    "budhvaar": 2, "budhvar": 2, "budhwar": 2, "budhwaar": 2,
    "guruvaar": 3, "guruvar": 3, "guruwar": 3, "guruwaar": 3, "brihaspativaar": 3, "brihaspativar": 3, "veervaar": 3,
    "shukravaar": 4, "shukravar": 4, "shukrawar": 4, "shukrawaar": 4,
    "shanivaar": 5, "shanivar": 5, "shaniwar": 5, "shaniwaar": 5, "shanichar": 5,
    "ravivaar": 6, "ravivar": 6, "raviwar": 6, "raviwaar": 6, "itvaar": 6, "itvar": 6, "itwar": 6, "itwaar": 6,
    "etvaar": 6, "etwar": 6,
    # Devanagari
    "सोमवार": 0, "मंगलवार": 1, "बुधवार": 2, "गुरुवार": 3, "बृहस्पतिवार": 3, "शुक्रवार": 4, "शनिवार": 5, "रविवार": 6,
    "इतवार": 6,
    # Marathi (the rest are shared with Hindi)
    "मंगळवार": 1, "गुरूवार": 3, "शुक्रवार": 4,
    "सोमवारी": 0, "मंगळवारी": 1, "बुधवारी": 2, "गुरुवारी": 3, "गुरूवारी": 3, "शुक्रवारी": 4, "शनिवारी": 5, "रविवारी": 6,
}

MONTH_WORDS: Dict[str, int] = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5, "june": 6,
    "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    "जनवरी": 1, "फरवरी": 2, "मार्च": 3, "अप्रैल": 4, "मई": 5, "जून": 6, "जुलाई": 7, "अगस्त": 8, "सितंबर": 9, "सितम्बर": 9,
    "अक्टूबर": 10, "नवंबर": 11, "नवम्बर": 11, "दिसंबर": 12, "दिसम्बर": 12,
}

# Marathi months, and the Hindi ones written with a nukta (फ़रवरी): after NFC a nukta letter is two code points.
MONTH_WORDS.update({unicodedata.normalize("NFC", w): m for w, m in {
    "फ़रवरी": 2, "जानेवारी": 1, "फेब्रुवारी": 2, "एप्रिल": 4, "मे": 5, "जुलै": 7, "ऑगस्ट": 8, "सप्टेंबर": 9,
    "ऑक्टोबर": 10, "नोव्हेंबर": 11, "डिसेंबर": 12, "अक्तूबर": 10, "नवंबर": 11}.items()})

RELATIVE_DAYS: Dict[str, int] = {
    "today": 0, "tonight": 0, "aaj": 0, "aj": 0, "आज": 0,
    "tomorrow": 1, "tmrw": 1, "kal": 1, "कल": 1,
    "day after tomorrow": 2, "overmorrow": 2,
    "parso": 2, "parson": 2, "parsoon": 2, "parsu": 2, "परसों": 2, "परसो": 2,
    "narso": 3, "narson": 3, "नरसों": 3,
    # Marathi
    "udya": 1, "उद्या": 1, "parva": 2, "परवा": 2,
}

NUMBER_WORDS: Dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
    "ek": 1, "do": 2, "teen": 3, "tin": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "pach": 5, "chhe": 6,
    "che": 6, "chah": 6, "chhah": 6, "saat": 7, "sat": 7, "aath": 8, "ath": 8, "nau": 9, "das": 10, "dus": 10,
    "gyarah": 11, "gyaarah": 11, "baarah": 12, "barah": 12, "bara": 12,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5, "छह": 6, "छः": 6, "छे": 6, "सात": 7, "आठ": 8, "नौ": 9,
    "दस": 10, "ग्यारह": 11, "बारह": 12,
    # Marathi
    "दोन": 2, "पाच": 5, "सहा": 6, "नऊ": 9, "दहा": 10, "अकरा": 11, "बारा": 12,
}
MINUTE_WORDS: Dict[str, int] = {"thirty": 30, "fifteen": 15, "fortyfive": 45, "forty-five": 45, "oclock": 0,
                                "o'clock": 0}

AM_WORDS = ("am", "a.m.", "a.m", "subah", "subha", "savere", "sawere", "morning", "सुबह", "सवेरे", "प्रातः",
            "sakali", "सकाळी", "सकाळ")
PM_WORDS = ("pm", "p.m.", "p.m", "dopahar", "dopaher", "dopahr", "sham", "shaam", "shyam", "raat", "rat", "afternoon",
            "evening", "night", "दोपहर", "शाम", "रात", "संध्या",
            "dupari", "दुपारी", "sandhyakali", "संध्याकाळी", "सायंकाळी", "ratri", "रात्री")
NEXT_WORDS = ("next", "coming", "agle", "agla", "agli", "aglay", "aane wale", "aane waale", "अगले", "अगला", "अगली", "आने वाले")

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


@dataclass
class Resolved:
    """What was understood. `notes` names assumptions a read-back may want to confirm."""
    date: Optional[date] = None
    time: Optional[time] = None
    assumed_meridiem: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.date is not None or self.time is not None


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFC", str(text or "")).translate(_DEVANAGARI_DIGITS).lower()
    text = text.replace("’", "'").replace("o'clock", "oclock").replace("a.m.", "am").replace("p.m.", "pm")
    text = re.sub(r"(\d)\s*(am|pm)\b", r"\1 \2", text)              # "11am" -> "11 am"
    text = re.sub(r"(\d)\.(\d{2})\b", r"\1:\2", text)              # "11.30" -> "11:30"
    text = re.sub(r"[,;!?।]+", " ", text)
    text = _FUSED.sub(r"\1 ", text)   # Marathi writes साडेपाच as one word
    return re.sub(r"\s+", " ", text).strip()


def _word_in(words, text: str) -> bool:
    return any(re.search(_B + re.escape(w) + _E, text) for w in words)


# ------------------------------------------------------------------------------------------------------- dates

def _days_until(target_wd: int, today: date, allow_today: bool) -> int:
    d = (target_wd - today.weekday()) % 7
    return 7 if d == 0 and not allow_today else d


def _explicit_date(text: str, today: date) -> Optional[date]:
    """27 october | 27th oct | october 27 | 27/10 | 27-10-2026 | 2026-10-27 | 27 अक्टूबर."""
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", text)
    if m and not re.search(r"\b\d{1,2}:\d{2}", m.group(0)):
        day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
        y = int(year) + (2000 if year and len(year) == 2 else 0) if year else None
        return _month_day(day, month, y, today)
    month_alt = "|".join(sorted(map(re.escape, MONTH_WORDS), key=len, reverse=True))
    m = re.search(_B + r"(\d{1,2})(?:st|nd|rd|th)?\s+(" + month_alt + r")" + _E + r"(?:\s+(\d{4}))?", text)
    if m:
        return _month_day(int(m.group(1)), MONTH_WORDS[m.group(2)], int(m.group(3)) if m.group(3) else None, today)
    m = re.search(_B + r"(" + month_alt + r")\s+(\d{1,2})(?:st|nd|rd|th)?" + _E + r"(?:\s+(\d{4}))?", text)
    if m:
        return _month_day(int(m.group(2)), MONTH_WORDS[m.group(1)], int(m.group(3)) if m.group(3) else None, today)
    return None


def _month_day(day: int, month: int, year: Optional[int], today: date) -> Optional[date]:
    try:
        if year:
            return date(year, month, day)
        d = date(today.year, month, day)
    except ValueError:
        return None
    return d if d >= today else d.replace(year=today.year + 1)


def resolve_date(text: str, now: Optional[datetime] = None, *, allow_today_weekday: bool = False) -> Resolved:
    """The date a phrase names, relative to `now` (default: the current local time). Resolved.date is None when
    nothing date-like was found."""
    now = now or datetime.now()
    today = now.date()
    text = _clean(text)
    out = Resolved()
    if not text:
        return out
    explicit = _explicit_date(text, today)
    if explicit:
        out.date = explicit
        return out
    m = re.search(_B + r"(?:in\s+)?(\d{1,2})\s*(?:days?|din|दिन)(?:\s+(?:baad|bad|later|mein|me|में|बाद))?" + _E, text)
    if m:
        out.date = today + timedelta(days=int(m.group(1)))
        return out
    if re.search(_B + r"(?:next week|agle hafte|agle saptah|agle week|अगले हफ्ते|अगले सप्ताह)" + _E, text):
        out.date = today + timedelta(days=7)
        return out
    for word in sorted(RELATIVE_DAYS, key=len, reverse=True):
        if re.search(_B + re.escape(word) + _E, text):
            out.date = today + timedelta(days=RELATIVE_DAYS[word])
            if word in ("kal", "कल"):
                out.notes.append("kal read as tomorrow")
            return out
    for word in sorted(WEEKDAY_WORDS, key=len, reverse=True):
        if re.search(_B + re.escape(word) + _E, text):
            is_next = _word_in(NEXT_WORDS, text)
            days = _days_until(WEEKDAY_WORDS[word], today, allow_today=allow_today_weekday and not is_next)
            out.date = today + timedelta(days=days)
            if is_next and days <= 2:
                out.notes.append("next <weekday> read as the coming one; the caller may mean the week after")
            return out
    return out


# ------------------------------------------------------------------------------------------------------- times

def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


_FUSED = re.compile("(" + "|".join(_nfc(w) for w in ("साडे", "सव्वा", "पावणे", "साढ़े", "पौने")) + ")(?=[ऀ-ॿ])")


# Hour words: the table above, normalised the way `_clean` normalises the caller's text (a nukta letter such as "ढ़" is two
# code points after NFC, so a pattern typed with the one-code-point form would never match).
HOUR_WORDS: Dict[str, int] = {_nfc(w): v for w, v in NUMBER_WORDS.items()}
HOUR_WORDS.update({_nfc(w): v for w, v in {"paach": 5, "chhah": 6, "nao": 9, "gyaarah": 11, "baraah": 12,
                                            "सहा": 6, "छ": 6}.items()})
_HOUR_ALT = "|".join(sorted(map(re.escape, HOUR_WORDS), key=len, reverse=True))
_NUM = r"(\d{1,2}|" + _HOUR_ALT + r")"


def _alt(words) -> str:
    return "(?:" + "|".join(sorted((re.escape(_nfc(w)) for w in words), key=len, reverse=True)) + ")"


_HALF = _alt(("saade", "sadhe", "saadhe", "sade", "साढ़े", "साढे", "साडे", "half past", "half"))
_SAWA = _alt(("sawa", "sava", "savva", "sawwa", "सवा", "सव्वा", "quarter past", "a quarter past"))
_PAUNE = _alt(("paune", "poune", "pone", "pavne", "pavane", "पौने", "पावणे", "quarter to", "a quarter to"))
_DEDH = _alt(("dedh", "derh", "डेढ़", "डेढ", "दीड", "deed"))
_DHAI = _alt(("dhai", "dhaai", "ढाई", "अडीच", "adeech", "adich"))
_CUE = _alt(("baje", "bje", "बजे", "वाजता", "वाजले", "oclock", "am", "pm", "bajkar", "बजकर"))
_MINUTE_ALT = "|".join(sorted(map(re.escape, MINUTE_WORDS), key=len, reverse=True))

_MORNING = ("subah", "subha", "savere", "sawere", "morning", "सुबह", "सवेरे", "प्रातः", "sakali", "सकाळी", "सकाळ")
_NOONISH = ("dopahar", "dopaher", "dopahr", "afternoon", "दोपहर", "dupari", "दुपारी")
_EVENING = ("sham", "shaam", "shyam", "evening", "शाम", "संध्या", "sandhyakali", "संध्याकाळी", "सायंकाळी", "sayankali")
_NIGHT = ("raat", "rat", "night", "रात", "ratri", "रात्री")

# People: "चार लोग", "char log", "for four people", "table for 4": a party size, not an hour.
_PEOPLE = _alt(("people", "persons", "person", "guests", "guest", "pax", "adults", "adult", "kids", "children", "members",
                "log", "लोग", "लोगों", "जण", "व्यक्ती", "व्यक्ति", "आदमी", "admi", "aadmi", "bachche", "बच्चे", "मंडळी"))
_PARTY_BEFORE = re.compile(_B + _NUM + r"\s*" + _PEOPLE + _E)
_PARTY_FOR = re.compile(_B + r"(?:table|party|group|room|booking|reservation)?\s*(?:for|of)\s+" + _NUM + r"\s*" + _PEOPLE + _E)
_PARTY_FOR_NUM = re.compile(_B + r"(?:table|party|group|room)\s+(?:for|of)\s+(\d{1,3})(?!\s*(?:am|pm|:|nights?))" + _E)
_PARTY_FOR_PLAIN = re.compile(_B + r"for\s+(\d{1,3})(?!\s*(?:am|pm|:|nights?|baje|बजे))" + _E)

_AMPM = re.compile(_B + r"(?:\d{1,2}(?::\d{2})?|" + _HOUR_ALT + r")\s*(am|pm)" + _E)


def _drop_party(text: str) -> str:
    for rx in (_PARTY_FOR, _PARTY_BEFORE, _PARTY_FOR_NUM, _PARTY_FOR_PLAIN):
        text = rx.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _drop_phone_digits(text: str) -> str:
    """A phone number said as digits or digit words is not a time: its digit run is taken out before the time is read."""
    try:
        from services import spoken_digits  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.services import spoken_digits  # noqa: PLC0415
    if spoken_digits.longest_run_length(text) >= 5:
        return spoken_digits.strip_digit_runs(text, 5)
    return text


def _value(token: str) -> int:
    return int(token) if token.isdigit() else HOUR_WORDS[token]


def opening_ranges(schedule: Optional[dict], day: Optional[date] = None) -> List[Tuple[int, int]]:
    """The opening hours of a schedule as (start minute, end minute) pairs: those of `day`, or of every day of the week
    when the day is not known. [] for a schedule without hours (a stay, or none)."""
    week = (schedule or {}).get("week") or {}
    keys = [("mon", "tue", "wed", "thu", "fri", "sat", "sun")[day.weekday()]] if day else list(week)
    out: List[Tuple[int, int]] = []
    for k in keys:
        for r in week.get(k) or []:
            try:
                a, b = (int(x[:2]) * 60 + int(x[3:5]) for x in (str(r["start"]), str(r["end"])))
            except (KeyError, ValueError, TypeError):
                continue
            out.append((a, b))
    return out


def _inside(minute: int, ranges) -> bool:
    return any(a <= minute < b for a, b in ranges)


def _meridiem(hour: int, minute: int, text: str, opening) -> Tuple[int, bool, Optional[str]]:
    """(24-hour hour, whether am/pm was assumed, a note) for a clock reading of `hour` (1-12 or already 24-hour)."""
    if hour > 12 or hour == 0:
        return hour, False, None
    m = _AMPM.search(text)
    if m:
        return (hour % 12 if m.group(1) == "am" else (hour % 12) + 12), False, None
    if _word_in(_NIGHT, text):
        return (0 if hour == 12 else hour + 12 if hour >= 6 else hour), False, None
    if _word_in(_EVENING, text):
        return (hour if hour == 12 else hour + 12), False, None
    if _word_in(_NOONISH, text):
        return (hour if (hour == 12 or hour >= 10) else hour + 12), False, None
    if _word_in(_MORNING, text):
        return (0 if hour == 12 else hour), False, None
    ranges = opening() if callable(opening) else (opening or [])
    if ranges and hour != 12:
        am, pm = hour % 12, hour % 12 + 12
        in_am, in_pm = _inside(am * 60 + minute, ranges), _inside(pm * 60 + minute, ranges)
        if in_am != in_pm:
            chosen = am if in_am else pm
            return chosen, True, f"{hour}:{minute:02d} read as {'am' if in_am else 'pm'} because only that falls inside the opening hours"
    if 1 <= hour <= 7:          # "5 baje" at a clinic or office is the evening
        return hour + 12, True, None
    if 8 <= hour <= 11:         # "9 baje" is the morning
        return hour, True, None
    return hour, False, None    # 12 stays noon


def _clock_reading(text: str) -> Optional[Tuple[int, int, bool]]:
    """(hour, minute, explicit 24-hour) of the clock time in `text` (before am/pm is decided), or None."""
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        return hour, minute, hour > 12 or hour == 0
    if re.search(_B + _DEDH + _E, text):
        return 1, 30, False
    if re.search(_B + _DHAI + _E, text):
        return 2, 30, False
    for rx, minute in ((_HALF, 30), (_SAWA, 15)):
        m = re.search(_B + rx + r"\s+" + _NUM + _E, text)
        if m:
            return _value(m.group(1)), minute, False
    m = re.search(_B + _PAUNE + r"\s+" + _NUM + _E, text)
    if m:
        h = _value(m.group(1))
        return (h - 1 if h > 1 else 12), 45, False
    m = re.search(_B + _NUM + r"\s*(?:bajkar|बजकर)\s*(\d{1,2})" + _E, text)       # "4 बजकर 30 मिनट"
    if m and int(m.group(2)) < 60:
        return _value(m.group(1)), int(m.group(2)), False
    m = re.search(_B + _NUM + r"\s+(" + _MINUTE_ALT + r")" + _E, text)            # "ten thirty", "five fifteen"
    if m:
        return _value(m.group(1)), MINUTE_WORDS[m.group(2)], False
    m = re.search(_B + _NUM + r"\s*" + _CUE + _E, text)                           # "5 baje", "paanch baje", "4 pm", "ek baje"
    if m:
        return _value(m.group(1)), 0, False
    period = _alt((*_MORNING, *_NOONISH, *_EVENING, *_NIGHT))
    m = re.search(_B + period + r"(?:\s+(?:ko|को|ला|at))?\s+" + _NUM + _E, text) \
        or re.search(_B + r"at\s+" + _NUM + _E, text) \
        or re.search(_B + _NUM + r"\s+(?:in the\s+)?" + period + _E, text)        # "subah 10", "sham ko 6", "3 in the afternoon"
    if m:
        return _value(m.group(1)), 0, False
    m = re.search(r"\b(\d{1,2})\b", text)                                         # a bare numeral: "tomorrow at 11", "10 30"
    if m and int(m.group(1)) <= 24:
        hour = int(m.group(1))
        m2 = re.search(r"\b(\d{1,2})\s+(\d{2})\b", text)
        minute = int(m2.group(2)) if m2 and int(m2.group(1)) == hour and int(m2.group(2)) < 60 else 0
        return hour, minute, False
    return None


def resolve_time(text: str, opening: Any = None) -> Resolved:
    """The time of day a phrase names. Resolved.time is None when nothing time-like was found.

    `opening` (optional) is the schedule's opening hours, a list of (start minute, end minute) or a function returning
    one (`opening_ranges`): a time with no am/pm or time-of-day word is read the way that falls inside them ("4:30" at a
    clinic open 9:00 to 18:00 is 4:30 pm; "9" is 9 am).

    A run of five or more spoken digits (a phone number) is not a time; "चार लोग", "char log" and "for four people" are a
    party size, not an hour; a number word on its own ("ek", "one") is not a time without baje, am/pm or a time of day."""
    text = _clean(text)
    out = Resolved()
    if not text:
        return out
    text = _drop_party(_drop_phone_digits(text))
    if re.search(_B + r"(?:noon|midday|dopahar 12|दोपहर 12)" + _E, text) and not re.search(r"\d\s*(?:baje|बजे|:)", text):
        out.time = time(12, 0)
        return out
    if re.search(_B + r"midnight" + _E, text):
        out.time = time(0, 0)
        return out
    reading = _clock_reading(text)
    if reading is None:
        return out
    hour, minute, explicit_24h = reading
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        return out
    if explicit_24h:
        out.time = time(hour, minute)
        return out
    hour, assumed, note = _meridiem(hour, minute, text, opening)
    out.assumed_meridiem = assumed
    if note:
        out.notes.append(note)
    out.time = time(hour % 24, minute)
    return out


@dataclass
class TimeMention:
    """A time of day named in a text: `time` as read (am/pm decided by the words around it, else the 1-7 pm rule),
    `explicit` when the text said am/pm, a time of day or a 24-hour time, and where it stands in the cleaned text."""
    time: time
    explicit: bool
    start: int
    end: int

    @property
    def key(self) -> Tuple[int, int]:
        """The time on a 12-hour dial: 4:30 am and 4:30 pm are the same key."""
        return (self.time.hour % 12, self.time.minute)

    def matches(self, wanted: time) -> bool:
        """It names `wanted`: the same time, or the same on the dial when the text gave no am/pm."""
        return self.time == wanted or (not self.explicit and self.key == (wanted.hour % 12, wanted.minute))


_MENTION_PERIOD = _alt((*_MORNING, *_NOONISH, *_EVENING, *_NIGHT))


def time_mentions(text: str) -> List[TimeMention]:
    """Every clock time a text names, in the forms an agent speaks them in English, Hindi and Marathi: 7 pm, 7:15 pm, 19:00,
    "शाम 7 बजे", "साढ़े सात बजे", "सवा सात", "पौने आठ", "डेढ़ बजे", "ढाई बजे", "साडे सात वाजता", "पावणे आठ", "दीड", "अडीच",
    "7 बजकर 15 मिनट", "half past six", "quarter to seven". A day or date number is never a time; a bare "at 7" is not
    one either (an agent that means 7 says pm). Used to check that a read-back names one time and the right one."""
    t = _clean(text)
    out: List[TimeMention] = []
    taken: List[Tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    def segment(a: int) -> str:
        """The sentence the mention is in (a time of day in another sentence is not about it)."""
        lo = max(t.rfind(".", 0, a), t.rfind("।", 0, a), -1)
        hi = [i for i in (t.find(".", a), t.find("।", a)) if i >= 0]
        return t[lo + 1:(min(hi) if hi else len(t))]

    def add(a: int, b: int, hour: int, minute: int, *, ampm: Optional[str] = None, h24: bool = False) -> None:
        if not free(a, b) or not (0 <= hour <= 24) or not (0 <= minute <= 59):
            return
        if h24 or hour > 12 or hour == 0:
            hh, explicit = hour % 24, True
        elif ampm:
            hh, explicit = (hour % 12 if ampm == "am" else hour % 12 + 12), True
        else:
            seg = segment(a)
            hh, _assumed, _ = _meridiem(hour, minute, seg, None)
            explicit = bool(re.search(_B + _MENTION_PERIOD + _E, seg))
        out.append(TimeMention(time(hh % 24, minute), explicit, a, b))
        taken.append((a, b))

    for m in re.finditer(r"(?<![\d:])(\d{1,2}):(\d{2})(?!\d)\s*(am|pm)?", t):
        h = int(m.group(1))
        add(m.start(), m.end(), h, int(m.group(2)), ampm=m.group(3), h24=h > 12 or h == 0 or h == 24)
    for m in re.finditer(_B + _NUM + r"\s*(am|pm)" + _E, t):
        add(m.start(), m.end(), _value(m.group(1)), 0, ampm=m.group(2))
    for rx, minute in ((_HALF, 30), (_SAWA, 15)):
        for m in re.finditer(_B + rx + r"\s+" + _NUM + _E + r"(?:\s*" + _CUE + _E + r")?", t):
            add(m.start(), m.end(), _value(m.group(1)), minute)
    for m in re.finditer(_B + _PAUNE + r"\s+" + _NUM + _E + r"(?:\s*" + _CUE + _E + r")?", t):
        h = _value(m.group(1))
        add(m.start(), m.end(), h - 1 if h > 1 else 12, 45)
    for m in re.finditer(_B + _DEDH + r"\s*" + _CUE + _E, t):
        add(m.start(), m.end(), 1, 30)
    for m in re.finditer(_B + _DHAI + r"\s*" + _CUE + _E, t):
        add(m.start(), m.end(), 2, 30)
    for m in re.finditer(_B + _NUM + r"\s*(?:bajkar|बजकर)\s*(\d{1,2})(?:\s*(?:minute|minutes|mins?|मिनट|मिनिटे))?" + _E, t):
        if int(m.group(2)) < 60:
            add(m.start(), m.end(), _value(m.group(1)), int(m.group(2)))
    for m in re.finditer(_B + _NUM + r"\s*(?:baje|bje|बजे|वाजता|वाजले|oclock)" + _E, t):
        add(m.start(), m.end(), _value(m.group(1)), 0)
    return sorted(out, key=lambda x: x.start)


# ------------------------------------------------------------------------------------------------------- both

def resolve(text: str, now: Optional[datetime] = None, opening: Any = None) -> Resolved:
    """Date and time from one phrase ("kal subah 10 baje", "next Monday 11 am"). A bare weekday that is today counts
    as today when the time named has not passed yet."""
    now = now or datetime.now()
    t = resolve_time(text, opening)
    allow_today = bool(t.time and t.time > now.time())
    d = resolve_date(text, now, allow_today_weekday=allow_today)
    return Resolved(d.date, t.time, t.assumed_meridiem, d.notes + t.notes)


def normalise_tool_args(args: dict, now: Optional[datetime] = None, opening: Any = None) -> Tuple[dict, List[str]]:
    """Rewrite `date` and `time` arguments a model passed in the caller's words into YYYY-MM-DD and HH:MM. Values
    already in those shapes are left alone; values nothing can be made of are left alone too (the tool then says what
    shape it wants). Returns the new arguments and the notes (assumptions) made."""
    if not isinstance(args, dict):
        return args, []
    raw_date = args.get("date")
    raw_time = args.get("time")
    date_ok = isinstance(raw_date, str) and bool(ISO_DATE_RE.match(raw_date.strip()))
    time_ok = raw_time is None or (isinstance(raw_time, str) and bool(HHMM_RE.match(raw_time.strip())))
    if (raw_date is None or date_ok) and time_ok:
        return args, []
    now = now or datetime.now()
    # An ISO date is not part of the phrase: its digits would be read as the hour of "2026-11-12 6 pm".
    phrase = " ".join(str(v) for v in (None if date_ok else raw_date, raw_time) if isinstance(v, str) and v.strip())
    res = resolve(phrase, now, opening)
    out = dict(args)
    notes = list(res.notes)
    if raw_date is not None and not date_ok:
        if res.date:
            out["date"] = res.date.isoformat()
            notes.append(f"date '{raw_date}' read as {out['date']}")
        else:
            notes.append(f"date '{raw_date}' not understood")
    if raw_time is not None and not time_ok:
        if res.time:
            out["time"] = res.time.strftime("%H:%M")
            notes.append(f"time '{raw_time}' read as {out['time']}" + (" (am/pm assumed)" if res.assumed_meridiem else ""))
        else:
            notes.append(f"time '{raw_time}' not understood")
    return out, notes


# ------------------------------------------------------------------------------------------------------- a request

@dataclass
class Request:
    """What a caller's phrase asks for: a day, a time of day, a number of nights and a party size (each None when absent)."""
    date: Optional[date] = None
    time: Optional[time] = None
    nights: Optional[int] = None
    party: Optional[int] = None
    assumed_meridiem: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return any(x is not None for x in (self.date, self.time, self.nights))


_COUNT_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "chhe": 6,
                "saat": 7, "aath": 8, "nau": 9, "das": 10, "एक": 1, "दो": 2, "दोन": 2, "तीन": 3, "चार": 4, "पाँच": 5,
                "पाच": 5, "छह": 6, "सहा": 6, "सात": 7, "आठ": 8, "नौ": 9, "नऊ": 9, "दस": 10, "दहा": 10}
_COUNT_ALT = "|".join(sorted(map(re.escape, _COUNT_WORDS), key=len, reverse=True))
_NIGHTS_RE = re.compile(_B + r"(\d{1,2}|" + _COUNT_ALT + r")\s*(?:nights?|raat(?:ein|on)?|रात(?:ें|ों)?|रात्री)" + _E)
_PARTY_RES = (re.compile(_B + r"(?:party|group|table)\s+of\s+(\d{1,3}|" + _COUNT_ALT + ")" + _E),
              re.compile(_B + r"(\d{1,3}|" + _COUNT_ALT + r")\s*(?:people|persons?|guests?|pax|adults?|log|लोग|लोगों|जण|व्यक्ती|व्यक्ति)" + _E),
              re.compile(_B + r"for\s+(\d{1,3})(?!\s*(?:am|pm|:|nights?))" + _E))
_MONTH_ALT = "|".join(sorted(map(re.escape, MONTH_WORDS), key=len, reverse=True))
_DATE_PHRASES = (re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
                 re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b"),
                 re.compile(_B + r"\d{1,2}(?:st|nd|rd|th)?\s+(?:" + _MONTH_ALT + r")" + _E + r"(?:\s+\d{4})?"),
                 re.compile(_B + r"(?:" + _MONTH_ALT + r")\s+\d{1,2}(?:st|nd|rd|th)?" + _E + r"(?:\s+\d{4})?"),
                 re.compile(_B + r"(?:in\s+)?\d{1,2}\s*(?:days?|din|दिन)(?:\s+(?:baad|bad|later|mein|me|में|बाद))?" + _E))


def _count(word: str) -> Optional[int]:
    return int(word) if word.isdigit() else _COUNT_WORDS.get(word)


def resolve_request(text: str, now: Optional[datetime] = None, opening: Any = None) -> Request:
    """Day, time, nights and party size from a question or a request: "is Thursday 6 pm free", "kal shaam 6 baje",
    "parso subah 10", "next Monday 4 pm", "2 nights from 12 November", "उद्या संध्याकाळी 6 वाजता". The pieces that are not
    there stay None. A date with digits in it ("27 October 4 pm") does not confuse the time."""
    now = now or datetime.now()
    out = Request()
    clean = _clean(text)
    if not clean:
        return out
    m = _NIGHTS_RE.search(clean)
    if m and not re.search(_B + r"(?:baje|बजे|वाजता|am|pm)" + _E, clean[m.end():m.end() + 8]):
        out.nights = _count(m.group(1))
        clean = (clean[:m.start()] + " " + clean[m.end():]).strip()
    for rx in _PARTY_RES:
        m = rx.search(clean)
        if m:
            out.party = _count(m.group(1))
            clean = (clean[:m.start()] + " " + clean[m.end():]).strip()
            break
    day = resolve_date(clean, now, allow_today_weekday=True)
    timeless = clean
    for rx in _DATE_PHRASES:
        timeless = rx.sub(" ", timeless)
    t = resolve_time(timeless, (lambda: opening(day.date)) if callable(opening) else opening)
    if day.date is not None and not t.time is None and day.date == now.date() and t.time <= now.time():
        day = resolve_date(clean, now, allow_today_weekday=False)   # a weekday named today whose time has passed
    elif day.date is not None and t.time is None:
        day = resolve_date(clean, now, allow_today_weekday=True)
    out.date, out.time, out.assumed_meridiem = day.date, t.time, t.assumed_meridiem
    out.notes = day.notes + t.notes
    return out
