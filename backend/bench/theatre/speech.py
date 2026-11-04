"""Words for the scripted callers: days, times, digits and numbers of people in Hindi, Marathi, Hinglish and English; the
times and digits an agent's reply names; and the placeholder formatter for a caller's lines.

A caller's line is written once with placeholders ({day}, {time}, {phone}, ...). `fmt(line, ctx, lang, mode)` fills them for the
screen (`mode="text"`: digits, the way a transcript shows them) or for the voice (`mode="tts"`: words, the way a person says
them, which a voice reads better than digits). Languages: "en", "hi", "mr", "hinglish" (Hindi grammar, English nouns; shown in
Roman letters, spoken from the Devanagari of the line's `tts`)."""
from __future__ import annotations

import re
import string
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

HI_HOURS = {1: "एक", 2: "दो", 3: "तीन", 4: "चार", 5: "पाँच", 6: "छह", 7: "सात", 8: "आठ", 9: "नौ", 10: "दस", 11: "ग्यारह",
            12: "बारह"}
MR_HOURS = {1: "एक", 2: "दोन", 3: "तीन", 4: "चार", 5: "पाच", 6: "सहा", 7: "सात", 8: "आठ", 9: "नऊ", 10: "दहा", 11: "अकरा",
            12: "बारा"}
EN_NUM = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
HI_DIGITS = ["शून्य", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ"]
MR_DIGITS = ["शून्य", "एक", "दोन", "तीन", "चार", "पाच", "सहा", "सात", "आठ", "नऊ"]
ROMAN_DIGITS = ["zero", "ek", "do", "teen", "chaar", "paanch", "chhe", "saat", "aath", "nau"]
WEEKDAYS = {
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "hi": ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
    "mr": ["सोमवारी", "मंगळवारी", "बुधवारी", "गुरुवारी", "शुक्रवारी", "शनिवारी", "रविवारी"],
}
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
             "December"]
HI_COUNT = {1: "एक", 2: "दो", 3: "तीन", 4: "चार", 5: "पाँच", 6: "छह"}
MR_COUNT = {1: "एक", 2: "दोन", 3: "तीन", 4: "चार", 5: "पाच", 6: "सहा"}
EN_COUNT = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}

LANGS = ("en", "hi", "mr", "hinglish")


def base_lang(lang: str) -> str:
    return "hi" if lang == "hinglish" else lang


def parse_hhmm(value: str) -> Tuple[int, int]:
    h, m = str(value).split(":")[:2]
    return int(h), int(m)


def _period(h: int, lang: str) -> str:
    if lang == "hi":
        return "सुबह" if h < 12 else "दोपहर" if h < 16 else "शाम" if h < 20 else "रात"
    if lang == "mr":
        return "सकाळी" if h < 12 else "दुपारी" if h < 16 else "संध्याकाळी" if h < 20 else "रात्री"
    return ""


def time_words(hhmm: str, lang: str, mode: str = "tts") -> str:
    """"शाम सवा छह बजे", "संध्याकाळी सहा वाजता", "6:15 pm", "shaam 6:15 baje"."""
    h, m = parse_hhmm(hhmm)
    h12 = h % 12 or 12
    if lang == "en":
        return f"{h12}:{m:02d} {'am' if h < 12 else 'pm'}" if m else f"{h12} {'am' if h < 12 else 'pm'}"
    if lang == "hinglish" and mode == "text":
        per = "subah" if h < 12 else "dopahar" if h < 16 else "shaam" if h < 20 else "raat"
        return f"{per} {h12}:{m:02d} baje" if m else f"{per} {h12} baje"
    base = base_lang(lang)
    if mode == "text":
        per = _period(h, base)
        return f"{per} {h12}:{m:02d} {'बजे' if base == 'hi' else 'वाजता'}" if m else \
            f"{per} {h12} {'बजे' if base == 'hi' else 'वाजता'}"
    hours = HI_HOURS if base == "hi" else MR_HOURS
    per = _period(h, base)
    nxt = (h12 % 12) + 1
    if m == 0:
        body = f"{hours[h12]} {'बजे' if base == 'hi' else 'वाजता'}"
    elif m == 15:
        body = f"{'सवा' if base == 'hi' else 'सव्वा'} {hours[h12]} {'बजे' if base == 'hi' else 'वाजता'}"
    elif m == 30:
        body = f"{'साढ़े' if base == 'hi' else 'साडे'} {hours[h12]} {'बजे' if base == 'hi' else 'वाजता'}"
    elif m == 45:
        body = f"{'पौने' if base == 'hi' else 'पावणे'} {hours[nxt]} {'बजे' if base == 'hi' else 'वाजता'}"
    else:
        body = f"{hours[h12]} {'बजकर' if base == 'hi' else 'वाजून'} {m} {'मिनट' if base == 'hi' else 'मिनिटे'}"
    return f"{per} {body}"


def weekday_word(d: date, lang: str, mode: str = "tts") -> str:
    if lang == "hinglish":
        return WEEKDAYS["en"][d.weekday()] if mode == "text" else WEEKDAYS["hi"][d.weekday()]
    return WEEKDAYS[base_lang(lang) if lang != "en" else "en"][d.weekday()]


def digits_words(digits: str, lang: str) -> str:
    table = {"en": EN_NUM, "hi": HI_DIGITS, "mr": MR_DIGITS, "hinglish": HI_DIGITS}[lang]
    return " ".join(table[int(c)] for c in digits if c.isdigit())


def digits_text(digits: str) -> str:
    d = re.sub(r"\D", "", digits)
    return f"{d[:5]} {d[5:]}" if len(d) == 10 else " ".join(re.findall(r"\d{1,4}", d))


def count_word(n: int, lang: str) -> str:
    table = {"en": EN_COUNT, "hi": HI_COUNT, "hinglish": HI_COUNT, "mr": MR_COUNT}[lang]
    return table.get(int(n), str(n))


def nights_phrase(n: int, lang: str, mode: str) -> str:
    if mode == "text" and lang in ("en", "hinglish"):
        return f"{n} night{'s' if n != 1 else ''}" if lang == "en" else f"{n} raat"
    c = count_word(n, lang)
    return {"en": f"{c} night{'s' if n != 1 else ''}", "hi": f"{c} रात", "hinglish": f"{c} रात", "mr": f"{c} रात्री"}[lang]


def party_phrase(n: int, lang: str, mode: str) -> str:
    c = count_word(n, lang)
    if mode == "text" and lang == "hinglish":
        return f"{n} log"
    return {"en": f"{c} people", "hi": f"{c} लोग", "hinglish": f"{c} लोग", "mr": f"{c} जण"}[lang]


class _Dict(dict):
    def __missing__(self, key):
        raise KeyError(key)


def build_words(ctx: Dict[str, Any], lang: str, mode: str) -> Dict[str, str]:
    """Every placeholder a caller line may use, for this language and mode."""
    out: Dict[str, str] = {}
    d: Optional[date] = ctx.get("date")
    if d is not None:
        out["day"] = weekday_word(d, lang, mode)
        out["date_long"] = f"{d.day} {MONTHS_EN[d.month - 1]}"
    if ctx.get("time"):
        out["time"] = time_words(ctx["time"], lang, mode)
    for key in ("later_time", "earlier_time"):
        if ctx.get(key):
            out[key] = time_words(ctx[key], lang, mode)
    for key in ("phone", "phone_slip", "aadhaar"):
        if ctx.get(key):
            digits = re.sub(r"\D", "", ctx[key])
            out[key] = digits_text(digits) if mode == "text" else digits_words(digits, lang)
            out[key + "_digits"] = digits
    if ctx.get("phone"):
        last4 = re.sub(r"\D", "", ctx["phone"])[-4:]
        out["last4"] = " ".join(last4) if mode == "text" else digits_words(last4, lang)
    if ctx.get("nights"):
        out["nights"] = nights_phrase(int(ctx["nights"]), lang, mode)
    if ctx.get("party"):
        out["party"] = party_phrase(int(ctx["party"]), lang, mode)
    for key in ("name", "name_tts"):
        if ctx.get(key):
            out[key] = ctx[key]
    return out


def fmt(template: str, ctx: Dict[str, Any], lang: str, mode: str = "text") -> str:
    """Fill {placeholders}; `{name_tts}` falls back to `{name}`. A placeholder with no value raises KeyError (a scenario
    that asks for something the run did not set up)."""
    words = build_words(ctx, lang, mode)
    if mode == "tts" and "name_tts" in words:
        words["name"] = words["name_tts"]
    return string.Formatter().vformat(template, (), _Dict(words))


def placeholders(template: str) -> List[str]:
    return [f for _, f, _, _ in string.Formatter().parse(template) if f]


# ------------------------------------------------------------------------------------------ what the agent said

_DEV = str.maketrans("०१२३४५६७८९", "0123456789")
_TIME_RE = re.compile(r"(?<![\d:.])(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?![\d:])", re.I)
_TIME_COLON = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)\s*(am|pm|a\.m\.|p\.m\.)?", re.I)
_TIME_AMPM = re.compile(r"(?<![\d:])(\d{1,2})\s*(am|pm|a\.m\.|p\.m\.)(?![a-z])", re.I)


def times_in(text: str) -> List[str]:
    """The clock times an agent's reply names, as "HH:MM" in order of appearance, without repeats. Reads "6:15 pm", "18:15",
    "5 pm" and Devanagari digits. A colon time without am/pm is taken as 24-hour when it is 13:00 or later, else with the
    evening rule for 1 to 7 (the clinic opens at 10) so "5:30" is 17:30. Dates (5 November) are not times."""
    low = (text or "").translate(_DEV)
    found: List[Tuple[int, str]] = []
    taken: List[Tuple[int, int]] = []
    for rx in (_TIME_COLON, _TIME_AMPM):
        for m in rx.finditer(low):
            if any(a <= m.start() < b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            h = int(m.group(1))
            minute = int(m.group(2)) if rx is _TIME_COLON and m.group(2) else 0
            mer = (m.group(3) if rx is _TIME_COLON else m.group(2)) or ""
            mer = mer.lower().replace(".", "")
            if mer == "pm" and h < 12:
                h += 12
            elif mer == "am" and h == 12:
                h = 0
            elif not mer and h < 8:
                h += 12
            if 0 <= h < 24 and 0 <= minute < 60:
                found.append((m.start(), f"{h:02d}:{minute:02d}"))
    out: List[str] = []
    for _, t in sorted(found):
        if t not in out:
            out.append(t)
    return out


_WORD_DIGITS = {}
for _i, _w in enumerate(EN_NUM):
    _WORD_DIGITS[_w] = str(_i)
for _table in (HI_DIGITS, MR_DIGITS, ROMAN_DIGITS):
    for _i, _w in enumerate(_table):
        _WORD_DIGITS.setdefault(_w, str(_i))
_WORD_DIGITS.update({"oh": "0", "ऑह": "0", "ज़ीरो": "0", "जीरो": "0", "zero": "0", "छः": "6", "छे": "6", "पांच": "5",
                     "दोन": "2", "नौ": "9", "नव": "9"})


def digit_string(text: str) -> str:
    """Every digit an agent or caller spoke, in order, as one string: numerals (also Devanagari) and digit words in English,
    Hindi, Marathi and Roman Hindi. Used to see whether a reply read a phone number back."""
    low = (text or "").lower().translate(_DEV)
    out: List[str] = []
    for tok in re.findall(r"\d|[^\s\d.,;:!?।()\-]+", low):   # Devanagari matras are not \w, so split on spaces and marks
        if tok.isdigit():
            out.append(tok)
        elif tok in _WORD_DIGITS:
            out.append(_WORD_DIGITS[tok])
    return "".join(out)


def says_digits(text: str, digits: str, minimum: int = 6) -> bool:
    """Did `text` say `digits` (all of them, or at least `minimum` in a row from the end of them)? Spaces, hyphens and digit
    words do not matter."""
    want = re.sub(r"\D", "", digits)
    got = digit_string(text)
    if not want or not got:
        return False
    if want in got:
        return True
    tail = want[-minimum:]
    return len(want) >= minimum and tail in got


def digit_string_loose(text: str) -> str:
    """The numerals of a text, with the digits of a time kept apart ("11 am" gives "11"): every run of digits separated by a space."""
    return " ".join(re.findall(r"\d+", (text or "").translate(_DEV)))
