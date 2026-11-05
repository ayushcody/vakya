"""The read-back rule: a booking is made only after the agent has read THIS booking back and the caller said yes.

`slot_readback_ok(text, date, time, name, phone)` says whether the agent's immediately preceding turn is such a read-back:

  * the exact date: the day of the month with the weekday or the month (or the ISO date), and no other date named;
  * the exact time, in any form the agent speaks it in English, Hindi or Marathi (7 pm, 7:15 pm, 19:00, "शाम 7 बजे",
    "साढ़े सात बजे", "पौने आठ", "साडे सात वाजता"...), hour and minutes, and no other time named: an offer of two times
    ("5:45 pm or 6:15 pm?") never counts;
  * the name (its first and last word);
  * the last four digits of the phone number, when one was given (as numerals, Devanagari digits or digit words);
  * after all of that, a question (the confirmation).

`is_affirmative(text, date, time)` says whether the caller's latest turn is a yes: haan, ha, ho, yes, theek hai, chalega,
kar do, बुक करो, हो, होय... and nothing else: not a no, not a change (a different time or date) and not a new request.

`sentences(args)` is the read-back to say, in English, Hindi and Marathi; the book tool's NOT DONE answer carries it, and
the sentences pass the check above (tests/test_readback_exact.py)."""
from __future__ import annotations

import re
import unicodedata
from datetime import date as Date, time as Time
from typing import Any, Dict, List, Optional

try:
    from services import spoken_digits, when
except ImportError:  # pragma: no cover
    from backend.services import spoken_digits, when

_QUESTION_END = ("?", "？", "؟")
_WORD = re.compile(r"[\w\u0900-\u097F]+")
_MONTH_ALT = "|".join(sorted(map(re.escape, when.MONTH_WORDS), key=len, reverse=True))
_LONG_WEEKDAYS = {w: i for w, i in when.WEEKDAY_WORDS.items() if len(w) >= 5 or not w.isascii()}


def _norm(text: Any) -> str:
    return unicodedata.normalize("NFC", str(text or "")).lower().translate(when._DEVANAGARI_DIGITS)   # noqa: SLF001


# ------------------------------------------------------------------------------------------------ the pieces

def _date_ok(low: str, d: Optional[Date]) -> bool:
    if d is None:
        return True
    pairs = set()
    for m in re.finditer(when._B + r"(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?(" + _MONTH_ALT + r")" + when._E, low):   # noqa: SLF001
        pairs.add((int(m.group(1)), when.MONTH_WORDS[m.group(2)]))
    for m in re.finditer(when._B + r"(" + _MONTH_ALT + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?!\d)(?!\s*(?::|am\b|pm\b|baje|बजे|वाजता|[.,]?\s*(?:am|pm)\b))", low):   # noqa: SLF001
        pairs.add((int(m.group(2)), when.MONTH_WORDS[m.group(1)]))
    for m in re.finditer(r"(?<![\d:.])(\d{1,2})[/-](\d{1,2})(?![\d:/-])", low):
        pairs.add((int(m.group(1)), int(m.group(2))))
    iso = [tuple(map(int, m.groups())) for m in re.finditer(r"(\d{4})-(\d{2})-(\d{2})", low)]
    if any(p != (d.year, d.month, d.day) for p in iso):
        return False
    if any(p != (d.day, d.month) for p in pairs):
        return False                                       # another date is named as well: an offer, not a read-back
    # another weekday named by its full name ("Thursday or Friday") is an offer too
    mine = {w for w, i in when.WEEKDAY_WORDS.items() if i == d.weekday()}
    if any(i != d.weekday() and re.search(when._B + re.escape(w) + when._E, low) for w, i in _LONG_WEEKDAYS.items()):   # noqa: SLF001
        return False
    if iso or pairs:
        return True
    names_day = any(re.search(when._B + re.escape(w) + when._E, low) for w in mine)   # noqa: SLF001
    number = re.search(rf"(?<![\d:./-]){d.day}(?:st|nd|rd|th)?(?![\d:])(?!\s*(?:am|pm|baje|बजे|वाजता))", low)
    return bool(names_day and number)


def _time_ok(text: str, t: Optional[Time]) -> bool:
    if t is None:
        return True
    mentions = when.time_mentions(text)
    return bool(mentions) and all(m.matches(t) for m in mentions) and len({m.key for m in mentions}) == 1


def _name_ok(low: str, name: Any) -> bool:
    words = [w for w in _WORD.findall(_norm(name)) if len(w) >= 2]
    if not words:
        return True
    have = set(_WORD.findall(low))
    return words[0] in have and words[-1] in have


def _tail_ok(low: str, phone: Any) -> bool:
    digits = re.sub(r"\D", "", _norm(phone))
    if len(digits) < 4:
        return True
    tail = digits[-4:]
    runs = [re.sub(r"\D", "", m.group(0)) for m in re.finditer(r"\d[\d\s\-•*]*\d", low)]
    runs += ["".join(d for d, _ in run) for run in spoken_digits.digit_token_runs(low)]
    return any(tail in r for r in runs)


def _nights_ok(low: str, nights: Any) -> bool:
    try:
        n = int(nights)
    except (TypeError, ValueError):
        return True
    word = {1: "one|a|1", 2: "two|do|2", 3: "three|teen|3", 4: "four|char|chaar|4", 5: "five|paanch|5"}.get(n, str(n))
    return bool(re.search(rf"(?:{word})\s*(?:nights?|raat|raatein|रात|रात्री|रात्र)", low)
                or (n == 1 and re.search(r"\bnight\b|रात", low)))


def _sentences(text: str) -> List[str]:
    return [s for s in re.split(r"(?<=[.!?।？؟])\s+", text.strip()) if s]


def missing(text: Optional[str], d: Optional[Date], t: Optional[Time], name: Any = None, phone: Any = None,
            nights: Any = None) -> List[str]:
    """What the agent's turn leaves out of the read-back: a list of "date", "time", "name", "phone", "nights",
    "question" (empty: it is one). A turn that names two times or two dates is reported as missing that part."""
    text = str(text or "")
    if not text.strip():
        return ["date", "time", "name", "phone", "question"]
    low = _norm(text)
    out: List[str] = []
    if not _date_ok(low, d) or (d is not None and not any(c.isdigit() for c in low)):
        out.append("date")
    if not _time_ok(text, t):
        out.append("time")
    if not _name_ok(low, name):
        out.append("name")
    if phone and not _tail_ok(low, phone):
        out.append("phone")
    if nights and not _nights_ok(low, nights):
        out.append("nights")
    sents = _sentences(text)
    asks = [i for i, s in enumerate(sents) if s.rstrip().endswith(_QUESTION_END)]
    if not asks:
        out.append("question")
    elif not out:
        # the question comes after the details: "Shall I book it? Thursday 12 November..." is not a read-back
        details = [i for i, s in enumerate(sents)
                   if (t is not None and when.time_mentions(s)) or (name and _first_word(name, _norm(s)))]
        if max(asks) < max(details or [0]):
            out.append("question")
    return out


def _first_word(name: Any, low: str) -> bool:
    words = [w for w in _WORD.findall(_norm(name)) if len(w) >= 2]
    return bool(words) and words[0] in set(_WORD.findall(low))


def slot_readback_ok(text: Optional[str], d: Optional[Date], t: Optional[Time], name: Any = None, phone: Any = None,
                     nights: Any = None) -> bool:
    return not missing(text, d, t, name, phone, nights)


# ------------------------------------------------------------------------------------------------ the caller's yes

_YES = re.compile(
    r"(?:^|\s)(?:yes|yeah|yep|yup|yea|ok|okay|okey|sure|correct|right|confirm(?:ed)?|fine|good|perfect|great|absolutely|"
    r"definitely|proceed|"
    r"haan|han|haa|haanji|hanji|ha ji|haan ji|ji haan|ji|ha|hn|hmm haan|theek hai|thik hai|theek|thik|sahi hai|sahi|"
    r"chalega|chalegi|chalta hai|kar do|kar dijiye|kar dijie|kardo|kar de|karo|pakka|bilkul|"
    r"ho|hoy|hoye|ho chalel|chalel|thik aahe|theek aahe|barobar|bara|"
    r"हाँ|हां|हा|हाँ जी|हां जी|जी|जी हाँ|जी हां|ठीक है|ठीक|सही है|सही|चलेगा|चलेगी|चलता है|कर दो|कर दीजिए|कर दीजिये|करो|"
    r"बुक करो|बुक कर दो|बुक कर दीजिए|पक्का|बिल्कुल|बिलकुल|हो|होय|होये|हो चालेल|चालेल|ठीक आहे|बरोबर|बरं|बर|"
    r"go ahead|go on|please do|do it|book it|book kar do|book karo|that works|sounds good|that's right|thats right|"
    r"that is right|that's correct|thats correct|that is correct|looks good|all good|all right|alright)(?=\s|$)")
_NO = re.compile(
    r"(?:^|\s)(?:no|nope|nah|not|don't|dont|do not|never|wait|stop|hold on|cancel|change|wrong|incorrect|instead|actually|"
    r"but|however|rather|different|another|other|"
    r"nahi|nahin|nai|nahee|mat|ruko|ruk|rukiye|galat|badal|badlo|badalna|lekin|magar|par|dusra|doosra|dusre|"
    r"नहीं|नही|नहि|मत|रुको|रुकिए|रुक|गलत|बदल|बदलो|बदलना|लेकिन|मगर|पर|दूसरा|दूसरे|"
    r"नको|नाही|नाहीं|थांबा|चुकीचे|चूक|बदला|पण|परंतु|दुसरा|दुसरे)(?=\s|$)")
_NEW = re.compile(r"(?:^|\s)(?:what|which|how|when|where|why|who|is it|are you|do you|can i|could i|can you|could you|"
                  r"available|free|timing|timings|price|cost|fee|fees|kya|kab|kitna|kitne|kaun|kaise|kahan|kyun|"
                  r"क्या|कब|कितना|कितने|कौन|कैसे|कहाँ|कहां|क्यों|काय|केव्हा|किती|कुठे|कोण|कसे)(?=\s|$)")


def is_affirmative(text: Optional[str], d: Optional[Date] = None, t: Optional[Time] = None) -> bool:
    """The caller's latest turn is a yes to what was read back: a yes-word, no no-word, no other time or date, no new question,
    and short (a long turn is a request, not a yes)."""
    raw = str(text or "")
    low = re.sub(r"[^\w\u0900-\u097F' ]+", " ", _norm(raw))
    low = re.sub(r"\s+", " ", low).strip()
    if not low or len(low.split()) > 12:
        return False
    if _NO.search(low) or _NEW.search(low) or not _YES.search(low):
        return False
    for m in when.time_mentions(raw):
        if t is not None and not m.matches(t):
            return False
    if d is not None:
        said = when.resolve_date(raw, None).date
        if said is not None and said != d:
            return False
    return True


# ------------------------------------------------------------------------------------------------ what to say

_WEEKDAYS = {"en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
             "hi": ("सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"),
             "mr": ("सोमवार", "मंगळवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार")}
_MONTHS = {"en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                  "November", "December"),
           "hi": ("जनवरी", "फरवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"),
           "mr": ("जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून", "जुलै", "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर",
                  "नोव्हेंबर", "डिसेंबर")}


def _time_words(t: Time, lang: str) -> str:
    h12 = t.hour % 12 or 12
    clock = f"{h12}:{t.minute:02d}" if t.minute else f"{h12}"
    if lang == "en":
        return f"{clock} {'am' if t.hour < 12 else 'pm'}"
    if lang == "hi":
        period = "सुबह" if 4 <= t.hour < 12 else "दोपहर" if 12 <= t.hour < 16 else "शाम" if 16 <= t.hour < 20 else "रात"
        return f"{period} {clock} बजे"
    period = "सकाळी" if 4 <= t.hour < 12 else "दुपारी" if 12 <= t.hour < 16 else "संध्याकाळी" if 16 <= t.hour < 20 else "रात्री"
    return f"{period} {clock} वाजता"


def sentences(args: Dict[str, Any], action: str = "book") -> Dict[str, str]:
    """The read-back of a booking in English, Hindi and Marathi, ending in the confirmation question. Dates, times, the name
    and the last four digits of the phone are written as they must be spoken (digits, never words). `action` "move" reads
    back the NEW day and time of an existing booking ("Moving your booking to Thursday 6 November at 4 pm. Shall I go
    ahead?")."""
    d = _date(args.get("date"))
    t = _time(args.get("time"))
    name = str(args.get("name") or "").strip()
    phone = re.sub(r"\D", "", str(args.get("phone") or ""))
    tail = " ".join(phone[-4:]) if len(phone) >= 4 else ""
    nights = args.get("nights")
    out: Dict[str, str] = {}
    for lang in ("en", "hi", "mr"):
        parts: List[str] = []
        if d:
            parts.append(f"{_WEEKDAYS[lang][d.weekday()]} {d.day} {_MONTHS[lang][d.month - 1]}")
        if t:
            parts.append(("at " if lang == "en" else "") + _time_words(t, lang))
        if nights:
            n = int(nights)
            parts.append({"en": f"for {n} night{'s' if n != 1 else ''}", "hi": f"{n} रात के लिए",
                          "mr": f"{n} रात्रींसाठी"}[lang])
        head = " ".join(parts)
        who = {"en": f"for {name}", "hi": f"{name} के नाम से", "mr": f"{name} यांच्या नावाने"}[lang] if name else ""
        number = {"en": f"phone number ending {tail}", "hi": f"फ़ोन नंबर के आख़िरी अंक {tail}",
                  "mr": f"फोन नंबरचे शेवटचे अंक {tail}"}[lang] if tail else ""
        if action == "move":
            lead = {"en": "Moving your booking to", "hi": "आपकी बुकिंग इस समय पर होगी:", "mr": "तुमची बुकिंग या वेळेवर असेल:"}[lang]
            ask = {"en": "Shall I go ahead?", "hi": "क्या मैं बदल दूँ?", "mr": "मी बदलू का?"}[lang]
            out[lang] = f"{lead} " + ", ".join(x for x in (head, who, number) if x) + ". " + ask
            continue
        ask = {"en": "Shall I book it?", "hi": "क्या मैं बुक कर दूँ?", "mr": "मी बुक करू का?"}[lang]
        out[lang] = ", ".join(x for x in (head, who, number) if x) + ". " + ask
    return out


def cancel_sentences(booking_id: str, day: Optional[Date], t: Optional[Time]) -> Dict[str, str]:
    """The read-back of a cancellation in English, Hindi and Marathi: one question that names the booking and its day and
    time ("Shall I cancel booking A12, Thursday 5 November at 3 pm?")."""
    out: Dict[str, str] = {}
    for lang in ("en", "hi", "mr"):
        parts: List[str] = []
        if day:
            parts.append(f"{_WEEKDAYS[lang][day.weekday()]} {day.day} {_MONTHS[lang][day.month - 1]}")
        if t:
            parts.append(("at " if lang == "en" else "") + _time_words(t, lang))
        head = " ".join(parts)
        bid = str(booking_id)
        out[lang] = {"en": f"Shall I cancel booking {bid}, {head}?",
                     "hi": f"क्या मैं बुकिंग {bid}, {head}, रद्द कर दूँ?",
                     "mr": f"मी बुकिंग {bid}, {head}, रद्द करू का?"}[lang]
    return out


def _date(value: Any) -> Optional[Date]:
    try:
        return Date.fromisoformat(str(value).strip()) if value else None
    except ValueError:
        return None


def _time(value: Any) -> Optional[Time]:
    try:
        h, m = str(value).strip().split(":")[:2]
        return Time(int(h), int(m)) if value else None
    except (ValueError, TypeError):
        return None
