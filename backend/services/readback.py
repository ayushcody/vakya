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

import os
import re
import unicodedata
from datetime import date as Date, datetime, time as Time, timezone
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


# ------------------------------------------------------------------------------------------------ expiry

PENDING_TTL_S = 180            # a read-back older than 3 minutes no longer counts
PENDING_MAX_CALLER_TURNS = 6   # nor one that the caller has talked past for 6 caller turns


def _limit(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, "") or default))
    except ValueError:
        return default


def parse_time(value: Any) -> Optional[datetime]:
    """An ISO timestamp (naive means UTC, as the local store writes them) as an aware datetime; None when unreadable."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        out = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def pending_expired(said_at: Any, caller_turns_since: Optional[int] = None, now: Optional[datetime] = None) -> bool:
    """A read-back said at `said_at`, since when the caller has spoken `caller_turns_since` times, has gone stale: older than
    3 minutes (READBACK_TTL_S) or 6 caller turns (READBACK_MAX_TURNS). A later "yes" then needs a fresh read-back. An unknown
    time or turn count never expires it by itself."""
    if caller_turns_since is not None and caller_turns_since >= _limit("READBACK_MAX_TURNS", PENDING_MAX_CALLER_TURNS):
        return True
    said = parse_time(said_at)
    if said is None:
        return False
    current = parse_time(now) if now is not None else datetime.now(timezone.utc)
    return (current - said).total_seconds() > _limit("READBACK_TTL_S", PENDING_TTL_S)


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


_PHONE_SENTENCE = re.compile(r"\b(?:number|phone|mobile|digits)\b|नंबर|नम्बर|फ़ोन|फोन|मोबाइल|\d{4}")
_FROM_TO = re.compile(r"\bfrom\b(.*?)\bto\b", re.S)


def _strip_from_times(sentence: str) -> str:
    """"Move A28 from Tue 10 Nov 5 PM to 6 PM?": the time of the booking as it stands is not an offer, drop it so only the new
    time is read."""
    m = _FROM_TO.search(sentence)
    if not m:
        return sentence
    inner = m.group(1)
    cleaned = re.sub(r"\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)", "", inner, flags=re.I)
    return sentence[:m.start(1)] + cleaned + sentence[m.end(1):]


def change_readback_ok(text: Optional[str], d: Optional[Date], t: Optional[Time]) -> bool:
    """`text` read back a CHANGE of an existing booking (a move to day `d` at `t`, or a cancellation of the booking at `d`, `t`):
    the caller is already identified and the number is on the booking, so a question about the phone number is not part of
    it, and statements about the booking as it stands now ("Got it, the Tuesday booking at 5 PM.") are not offers. What is
    left must be a normal read-back of the new details ending in the confirmation question."""
    text = str(text or "")
    if not text.strip():
        return False
    kept: List[str] = []
    for sent in _sentences(text):
        question = sent.rstrip().endswith(_QUESTION_END)
        if _PHONE_SENTENCE.search(_norm(sent)) and not (t is not None and when.time_mentions(sent)):
            continue                                                 # "Also, is the number ending 6295 correct?"
        sent = _strip_from_times(sent)
        if not question and (not _time_ok(sent, t) or not _date_ok(_norm(sent), d)):
            continue                                                 # the booking as it stands
        kept.append(sent)
    return slot_readback_ok(" ".join(kept), d, t)


# ------------------------------------------------------------------------------------------------ the caller's yes

_YES = re.compile(
    r"(?:^|\s)(?:yes|yeah|yep|yup|yea|ok|okay|okey|sure|correct|right|confirm(?:ed)?|fine|good|perfect|great|absolutely|"
    r"definitely|proceed|"
    r"haan|han|haa|haanji|hanji|ha ji|haan ji|ji haan|ji|ha|hn|hmm haan|theek hai|thik hai|theek|thik|sahi hai|sahi|"
    r"chalega|chalegi|chalta hai|kar do|kar dijiye|kar dijie|kardo|kar de|karo|pakka|bilkul|"
    r"ho|hoy|hoye|ho chalel|chalel|thik aahe|theek aahe|barobar|bara|"
    r"हाँ|हां|हा|हँ|हं|हूँ|हाँजी|हांजी|यस|येस|ओके|ओक|हाँ जी|हां जी|जी|जी हाँ|जी हां|ठीक है|ठीक|सही है|सही|चलेगा|चलेगी|चलता है|कर दो|कर दीजिए|कर दीजिये|करो|"
    r"बुक करो|बुक कर दो|बुक कर दीजिए|पक्का|बिल्कुल|बिलकुल|हो|होय|होये|हो चालेल|चालेल|ठीक आहे|बरोबर|बरं|बर|"
    r"go ahead|go on|please do|do it|book it|book kar do|book karo|that works|sounds good|that's right|thats right|"
    r"that is right|that's correct|thats correct|that is correct|looks good|all good|all right|alright)(?=[\s।॥,.!?]|$)")
_NO = re.compile(
    r"(?:^|\s)(?:no|nope|nah|not|don't|dont|do not|never|wait|stop|hold on|cancel|change|wrong|incorrect|instead|actually|"
    r"but|however|rather|different|another|other|"
    r"nahi|nahin|nai|nahee|mat|ruko|ruk|rukiye|galat|badal|badlo|badalna|lekin|magar|par|dusra|doosra|dusre|"
    r"नहीं|नही|नहि|मत|रुको|रुकिए|रुक|गलत|बदल|बदलो|बदलना|लेकिन|मगर|पर|दूसरा|दूसरे|"
    r"नको|नाही|नाहीं|थांबा|चुकीचे|चूक|बदला|पण|परंतु|दुसरा|दुसरे)(?=\s|$)")
_NEW = re.compile(r"(?:^|\s)(?:what|which|how|when|where|why|who|is it|are you|do you|can i|could i|can you|could you|"
                  r"available|free|timing|timings|price|cost|fee|fees|kya|kab|kitna|kitne|kaun|kaise|kahan|kyun|"
                  r"क्या|कब|कितना|कितने|कौन|कैसे|कहाँ|कहां|क्यों|काय|केव्हा|किती|कुठे|कोण|कसे)(?=\s|$)")


# Prisma often hears a phone caller's "yeah" as the Hindi "या" ("or"), alone or with a polite word: "या, please।". Straight
# after a read-back that is a yes: only when "या" opens the turn and nothing else but polite or yes words follows.
_MISHEARD_YEAH = re.compile(r"^(?:या|याह|येह|येअ|यॅ)(?=\s|$)")
_POLITE = {"please", "plz", "pls", "yes", "sure", "ok", "okay", "do", "it", "go", "ahead", "haan", "हाँ", "हां", "जी", "ji",
           "कर", "दो", "करो", "प्लीज", "प्लीज़", "यस"}


def _fix_misheard_yes(low: str) -> str:
    low = low.replace("।", " ").strip()
    m = _MISHEARD_YEAH.match(low)
    if not m:
        return low
    rest = low[m.end():].split()
    return "yeah " + " ".join(rest) if all(w in _POLITE for w in rest) else low


def leading_yes(text: Optional[str]) -> bool:
    """The caller's turn OPENS with a yes ("Yeah, sure. Monday, it's three pm"): a yes-word in its first three words and no
    no-word there. For an answer to a question about a detail, where the rest of the turn may carry another detail."""
    low = re.sub(r"[^\w\u0900-\u097F' ]+", " ", _norm(text))
    low = re.sub(r"\s+", " ", _fix_misheard_yes(re.sub(r"\s+", " ", low).strip()))
    head = " ".join(low.split()[:3])
    return bool(head) and bool(_YES.search(head)) and not _NO.search(head)


def mentions_whatsapp(text: Optional[str]) -> bool:
    """The agent's turn names WhatsApp (in Latin or Devanagari letters): the read-back carried the consent question."""
    return bool(re.search(r"whats\s?app|व्हॉट्स\s?ॲ?ए?प|व्हाट्स\s?ऐ?ए?प|वॉट्स\s?ऐ?ए?प|व्हॉट्सअ|व्हाट्सऐ|व्हाट्सएप", _norm(text)))


def is_affirmative(text: Optional[str], d: Optional[Date] = None, t: Optional[Time] = None) -> bool:
    """The caller's latest turn is a yes to what was read back: a yes-word, no no-word, no other time or date, no new question,
    and short (a long turn is a request, not a yes). A misheard "या" (Prisma's "yeah") counts too."""
    raw = str(text or "")
    low = re.sub(r"[^\w\u0900-\u097F' ]+", " ", _norm(raw))
    low = re.sub(r"\s+", " ", _fix_misheard_yes(re.sub(r"\s+", " ", low).strip())).replace("।", " ").strip()
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


def when_words(day: Optional[Date], t: Optional[Time], lang: str) -> str:
    """"बुधवार शाम 5 बजे" / "Wednesday 5 pm": the day and time a caller named, as they are said aloud (a filler that shows the
    caller was understood: services/turn_stream.py). Empty when neither is given."""
    lang = lang if lang in _WEEKDAYS else "en"
    parts: List[str] = []
    if day:
        parts.append(_WEEKDAYS[lang][day.weekday()])
    if t:
        parts.append(_time_words(t, lang))
    return " ".join(parts)


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


_WHATSAPP_CLAUSE = {"en": "and I'll send the confirmation on WhatsApp", "hi": "और मैं WhatsApp पर कन्फ़र्मेशन भेज दूँगी",
                    "mr": "आणि मी WhatsApp वर कन्फर्मेशन पाठवेन"}


_BOOKED = {
    "en": ("Booked.", "Your booking ID is {id}, {when}.", "Your token number is {token}.",
           "The confirmation will be sent on WhatsApp."),
    "hi": ("बुकिंग हो गई है।", "बुकिंग आईडी {id} है, {when}।", "आपका टोकन नंबर {token} है।",
           "पुष्टि का संदेश WhatsApp पर भेजा जाएगा।"),
    "mr": ("बुकिंग झाली आहे.", "बुकिंग आयडी {id} आहे, {when}.", "तुमचा टोकन नंबर {token} आहे.",
           "पुष्टीचा संदेश WhatsApp वर पाठवला जाईल."),
}


def booked_sentence(lang: str, booking_id: str, day: Optional[Date], t: Optional[Time], token: Any = None,
                    whatsapp: bool = False) -> str:
    """What the caller hears when a booking has been made, in English, Hindi or Marathi: that it is booked, its id with the day,
    date and time, the token number when the schedule gives one, and that the confirmation goes out on WhatsApp. The same
    facts the language model is given by the tool; services/tools.py `booked_line` says when this may stand in for it."""
    lang = lang if lang in _BOOKED else "en"
    head, ident, tok, wa = _BOOKED[lang]
    parts: List[str] = []
    if day:
        parts.append(f"{_WEEKDAYS[lang][day.weekday()]} {day.day} {_MONTHS[lang][day.month - 1]}")
    if t:
        parts.append(_time_words(t, lang))
    out = [head, ident.format(id=booking_id, when=", ".join(parts))]
    if token:
        out.append(tok.format(token=token))
    if whatsapp:
        out.append(wa)
    return " ".join(out)


def sentences(args: Dict[str, Any], action: str = "book", whatsapp: bool = False) -> Dict[str, str]:
    """The read-back of a booking in English, Hindi and Marathi, ending in the confirmation question. Dates, times, the name
    and the last four digits of the phone are written as they must be spoken (digits, never words). `action` "move" reads
    back the NEW day and time of an existing booking ("Moving your booking to Thursday 6 November at 4 pm. Shall I go
    ahead?"). `whatsapp` adds the WhatsApp confirmation to the same sentence, so the caller's one yes covers it too."""
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
        bits = [x for x in (head, who, number) if x] + ([_WHATSAPP_CLAUSE[lang]] if whatsapp else [])
        out[lang] = ", ".join(bits) + ". " + ask
    return out


def which_booking_sentence(lang: str, rows: List[Dict[str, Any]]) -> str:
    """"You have two bookings: Tuesday 10 November at 6 pm and Thursday 12 November at 5 pm. Which one?" for a caller who asks to
    change a booking and has more than one."""
    heads = []
    for r in rows[:4]:
        d, t = _date(r.get("date")), _time(r.get("time"))
        if d is None or t is None:
            continue
        heads.append(f"{_WEEKDAYS[lang][d.weekday()]} {d.day} {_MONTHS[lang][d.month - 1]} " + ("at " if lang == "en" else "") + _time_words(t, lang))
    if len(heads) < 2:
        return ""
    listed = ", ".join(heads[:-1]) + {"en": " and ", "hi": " और ", "mr": " आणि "}[lang] + heads[-1]
    return {"en": f"You have {len(heads)} bookings: {listed}. Which one should I change?",
            "hi": f"आपकी {len(heads)} बुकिंग हैं: {listed}। कौन सी बदलनी है?",
            "mr": f"तुमच्या {len(heads)} बुकिंग आहेत: {listed}. कोणती बदलू?"}[lang]


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
