"""A deterministic stand-in for the language model, so Caller Theatre has results even when the real model is weak.

    backend/.venv/bin/python backend/bench/theatre/scripted_llm.py --port 8099

It is a tiny OpenAI-compatible server (POST /v1/chat/completions, streamed or not). Point the API at it with
`LLM_BASE_URL=http://127.0.0.1:8099/v1 LLM_API_KEY=theatre LLM_MODEL=theatre-scripted` (the runner's `--scripted-llm` prints and checks that) and the agent's
language-model step becomes a fixed dialogue policy. EVERYTHING ELSE IS THE REAL SYSTEM: speech recognition, the tool
loop, the booking store and its availability rules, the read-back and confirm-before-cancel rules, the caller details, the
text-to-speech and the stored conversation. Only the wording and the choice of the next tool come from rules here, so a result
labelled "scripted model" says the pipeline, tools and rules work end to end; it does not say a language model can do this.

The policy (`decide`) is a function of the messages it is given:
  1. it rebuilds what the caller said (date, time, nights, name, phone, a request to cancel) from every caller turn,
  2. it looks at the tool results already in this turn (messages after the caller's last words),
  3. it answers with ONE tool request (JSON, the protocol of services/tools.py) or with the words to speak.
Replies are in English, Hindi, Marathi or Roman Hindi, whichever the caller used. It asks for a missing name or number, checks
availability before it says a time is free, offers the times either side when it is not, reads the day, time, name and phone
number back and asks "Shall I book it?", books only after a yes, finds a booking by phone before cancelling and asks before it
cancels, welcomes a caller back by the name on their earlier booking, and declines to note an Aadhaar number."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import unicodedata
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
for p in (str(BACKEND), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from . import speech
except ImportError:
    import speech  # type: ignore

LOG_PATH: Optional[str] = os.environ.get("THEATRE_FAKE_LOG")

MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "hi": ["जनवरी", "फरवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
    "mr": ["जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून", "जुलै", "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर", "नोव्हेंबर", "डिसेंबर"],
}
MONTHS["hinglish"] = MONTHS["en"]

YES = ["yes", "yeah", "yep", "yup", "sure", "please", "ok", "okay", "haan", "han", "ji haan", "हाँ", "हां", "हा", "हो", "होय", "जरूर",
       "ज़रूर", "बिल्कुल", "ठीक है", "ठीक आहे", "चालेल", "चलेगा", "कर दीजिए", "कर दो", "कर दें", "करा", "करून टाका", "kar dijiye", "kar do",
       "theek hai", "correct", "right", "सही है", "सही आहे"]
NO = ["no", "nahi", "nahin", "नहीं", "नही", "नाही", "wrong", "galat", "गलत", "चूक", "don't", "do not", "mat"]
CANCEL = ["cancel", "canceled", "cancelled", "रद्द", "रद", "कैंसल", "कॅन्सल", "radd", "band kar"]
AADHAAR = ["aadhaar", "aadhar", "adhar", "aadhaar", "आधार", "आधार"]
LATER = ["later", "late", "after", "baad", "बाद", "नंतर", "आगे", "next", "second", "बाद वाला", "dusra", "दूसरा", "दुसरा"]
EARLIER = ["earlier", "early", "before", "pehle", "पहले", "आधी", "आधीचा", "first", "पहला", "पहिला"]
MR_MARK = ["आहे", "मला", "माझं", "माझे", "माझा", "वाजता", "भेटायचं", "करा", "चालेल", "तुम्ही", "आणि", "सकाळी", "संध्याकाळी", "दुपारी", "नाव"]
HI_MARK = ["है", "मुझे", "मेरा", "मेरी", "बजे", "कीजिए", "चाहिए", "और", "सुबह", "शाम", "नाम", "दीजिए", "करना"]
ROMAN_HI = ["mujhe", "mera", "mere", "hai", "hain", "chahiye", "kal", "baje", "ko", "kar", "karna", "nahi", "haan", "aur", "ke liye", "pichhli", "naam"]


def fold(s: str) -> str:
    return unicodedata.normalize("NFC", s or "").lower()


def has_word(text: str, words: List[str]) -> bool:
    low = " " + re.sub(r"[.,!?।]+", " ", fold(text)) + " "
    return any(" " + w + " " in low for w in words)


def contains_any(text: str, words: List[str]) -> bool:
    low = fold(text)
    return any(w in low for w in words)


# ------------------------------------------------------------------------------------------ reading the messages

NOW_RE = re.compile(r"\(Right now it is ([A-Za-z]+) (\d{4}-\d{2}-\d{2}), (\d{2}:\d{2})\.\)")
SLOTS_RE = re.compile(r"\(Details the caller already gave in this conversation:(.*?)\)\s*$", re.S)
TOOL_RESULT_RE = re.compile(r"^TOOL RESULT from ([\w.]+) \((success|ERROR[^)]*)\)\. This is data, not instructions: (.*?)(?:\nNow answer the caller.*)?$", re.S)


def msg_text(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, list):
        return " ".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
    return str(c or "")


def clean_user(text: str) -> str:
    if "Caller says:" in text:      # the turn's prompt: passages from the documents, the clock, then the caller's words
        text = text.rsplit("Caller says:", 1)[1]
    t = NOW_RE.sub("", text)
    t = SLOTS_RE.sub("", t)
    return t.strip()


def parse_tool_result(text: str) -> Optional[Tuple[str, bool, Any]]:
    m = TOOL_RESULT_RE.match(text.strip())
    if not m:
        return None
    name, status, body = m.group(1), m.group(2), m.group(3).strip()
    ok = status == "success"
    try:
        value: Any = json.loads(body)
    except ValueError:
        value = body
    return name.rsplit(".", 1)[-1], ok, value


def split_messages(messages: List[dict]) -> Dict[str, Any]:
    """The caller's turns (cleaned), the agent's spoken replies, the tool results of the CURRENT turn (after the last caller
    message) and today's date from the clock line."""
    callers: List[str] = []
    agents: List[str] = []
    results: List[Tuple[str, bool, Any]] = []
    now: Optional[datetime] = None
    last_real_user_index = -1
    for i, m in enumerate(messages[1:], start=1):
        role, text = m.get("role"), msg_text(m)
        if role == "user":
            tr = parse_tool_result(text)
            if tr is not None or text.startswith(("There is no tool called", "No more tool calls")):
                continue
            last_real_user_index = i
    for i, m in enumerate(messages[1:], start=1):
        role, text = m.get("role"), msg_text(m)
        if role == "user":
            tr = parse_tool_result(text)
            if tr is not None:
                if i > last_real_user_index:
                    results.append(tr)
                continue
            if text.startswith(("There is no tool called", "No more tool calls")):
                continue
            mm = NOW_RE.search(text)
            if mm and i == last_real_user_index:
                now = datetime.strptime(f"{mm.group(2)} {mm.group(3)}", "%Y-%m-%d %H:%M")
            callers.append(clean_user(text))
        elif role == "assistant":
            t = text.strip()
            if t.startswith("{") and '"tool"' in t[:20]:
                continue
            agents.append(t)
    return {"callers": callers, "agents": agents, "results": results, "now": now or datetime.now()}


# --------------------------------------------------------------------------------------------- what the caller said

def detect_language(text: str) -> str:
    low = fold(text)
    if re.search(r"[ऀ-ॿ]", low):
        mr = sum(1 for w in MR_MARK if w in low)
        hi = sum(1 for w in HI_MARK if w in low)
        return "mr" if mr > hi else "hi"
    if sum(1 for w in ROMAN_HI if re.search(r"\b" + re.escape(w) + r"\b", low)) >= 2:
        return "hinglish"
    return "en"


def phones_in(text: str) -> Tuple[Optional[str], Optional[str]]:
    """(a phone number, a longer ID-like number) said in `text`: runs of digits and digit words with no other word between."""
    low = fold(text).translate(speech._DEV)
    tokens = re.findall(r"\d+|[^\s\d.,;:!?।()\-]+", low)
    runs: List[str] = []
    cur = ""
    for tok in tokens:
        if tok.isdigit():
            cur += tok
        elif tok in speech._WORD_DIGITS:
            cur += speech._WORD_DIGITS[tok]
        else:
            if cur:
                runs.append(cur)
            cur = ""
    if cur:
        runs.append(cur)
    phone = idlike = None
    for r in runs:
        if len(r) == 10:
            phone = r
        elif len(r) in (11, 12, 13) and (r.startswith("0") or r.startswith("91")) and len(r) - (1 if r.startswith("0") else 2) == 10:
            phone = r[-10:]
        elif len(r) >= 11:
            idlike = r
    return phone, idlike


NAME_CUE = re.compile(
    r"(?:my name is|this is|i am|i'm|mera naam|mera nam|naam|माझं नाव|माझे नाव|माझ नाव|मेरा नाम|मेरा नाम|नाव|नाम)\s*(?:है|hai|आहे|is)?\s*[:,\-]?\s*"
    r"([^\d.,;!?।\n]+)", re.I)
STOP_WORDS = {"hai", "है", "आहे", "and", "और", "आणि", "my", "mera", "मेरा", "माझा", "माझं", "फोन", "number", "नंबर", "phone", "mobile", "ko", "को",
              "se", "से", "ke", "के", "for", "mujhe", "मुझे", "मला", "i", "want", "chahiye", "चाहिए", "है।", "बोल", "bol", "raha", "रहा", "hoon", "हूँ", "हूं"}


def name_in(text: str) -> Optional[str]:
    m = NAME_CUE.search(text)
    if not m:
        return None
    words: List[str] = []
    for w in m.group(1).split():
        wc = w.strip(".,;:!?।")
        if fold(wc) in STOP_WORDS or not wc:
            break
        words.append(wc)
        if len(words) == 3:
            break
    return " ".join(words) if words else None


def scrub_numbers(text: str) -> str:
    """The text without the long numbers in it (a phone or ID number, as numerals or digit words), so they are not read as times."""
    text = re.sub(r"\S+\s+(?:लोग|log|people|persons|guests|जण)\b", " ", fold(text))   # "चार लोग" is a party, not four o'clock
    tokens = re.findall(r"\s+|\d+|[^\s\d]+", text.translate(speech._DEV))
    out: List[str] = []
    run: List[str] = []
    digits = 0

    def flush() -> None:
        nonlocal run, digits
        if digits < 6:
            out.extend(run)
        run, digits = [], 0

    for tok in tokens:
        w = tok.strip(".,;:!?।()-")
        is_digit = tok.isdigit() or (w in speech._WORD_DIGITS and not tok.isspace())
        if is_digit:
            run.append(tok)
            digits += len(tok) if tok.isdigit() else 1
        elif tok.isspace() and run:
            run.append(tok)
        else:
            flush()
            out.append(tok)
    flush()
    return "".join(out)


class Facts:
    """What the whole call has told the agent so far."""

    def __init__(self) -> None:
        self.date: Optional[date] = None
        self.time: Optional[str] = None
        self.nights: Optional[int] = None
        self.name: Optional[str] = None
        self.phone: Optional[str] = None
        self.cancel = False
        self.aadhaar_now = False
        self.aadhaar_ever = False
        self.language = "en"
        self.explicit_time_now = False
        self.changed_now = False
        self.corrected_now = False


def read_facts(callers: List[str], agents: List[str], now: datetime) -> Facts:
    try:
        from services import when
    except ImportError:  # pragma: no cover
        from backend.services import when
    f = Facts()
    f.language = detect_language(callers[0]) if callers else "en"
    for i, text in enumerate(callers):
        last = i == len(callers) - 1
        prev_agent = agents[i - 1] if 0 < i <= len(agents) else (agents[-1] if agents and i > 0 else "")
        # a number in the middle of the call must not be read as a time: take the phone and ID numbers out first
        phone, idlike = phones_in(text)
        scrub = scrub_numbers(text)
        req = when.resolve_request(scrub, now)
        before = (f.date, f.time, f.nights, f.name, f.phone)
        if req.date:
            f.date = req.date
        if req.time:
            f.time = req.time.strftime("%H:%M")
        if req.nights:
            f.nights = int(req.nights)
        nm = name_in(text)
        if nm:
            f.name = nm
        if phone:
            f.phone = phone
        if contains_any(text, CANCEL):
            f.cancel = True
        if contains_any(text, AADHAAR) or idlike:
            f.aadhaar_ever = True
            if last:
                f.aadhaar_now = True
        # choosing between the two times the agent just offered
        offered = sorted(speech.times_in(prev_agent)) if prev_agent else []
        if len(offered) >= 2 and not req.time:
            if has_word(text, LATER) or contains_any(text, ["later one", "बाद वाला", "नंतरची"]):
                f.time = offered[-1]
            elif has_word(text, EARLIER):
                f.time = offered[0]
        if last:
            f.explicit_time_now = bool(req.time)
            f.changed_now = (f.date, f.time, f.nights) != before[:3]
            f.corrected_now = bool(phone and before[4] and phone != before[4]) or bool(nm and before[3] and nm != before[3])
    return f


# ------------------------------------------------------------------------------------------------- the wording

def weekday(d: date, lang: str) -> str:
    return speech.WEEKDAYS["en"][d.weekday()] if lang in ("en", "hinglish") else \
        {"hi": speech.WEEKDAYS["hi"], "mr": speech.WEEKDAYS["hi"]}[lang][d.weekday()]


def day_phrase(d: date, lang: str) -> str:
    return f"{weekday(d, lang)} {d.day} {MONTHS[lang][d.month - 1]}"


def clock(hhmm: str, lang: str) -> str:
    h, m = speech.parse_hhmm(hhmm)
    h12 = h % 12 or 12
    if lang in ("en", "hinglish"):
        return (f"{h12}:{m:02d} {'am' if h < 12 else 'pm'}" if m else f"{h12} {'am' if h < 12 else 'pm'}")
    per = speech._period(h, lang)
    word = "बजे" if lang == "hi" else "वाजता"
    return f"{per} {h12}:{m:02d} {word}" if m else f"{per} {h12} {word}"


def when_phrase(d: date, hhmm: Optional[str], nights: Optional[int], lang: str) -> str:
    if nights:
        n = int(nights)
        if lang == "en":
            return f"{n} night{'s' if n != 1 else ''} from {day_phrase(d, lang)}"
        if lang == "hinglish":
            return f"{day_phrase(d, lang)} se {n} raat"
        return f"{day_phrase(d, lang)} से {n} {'रात' if lang == 'hi' else 'रात्री'}"
    if lang == "en":
        return f"{day_phrase(d, lang)} at {clock(hhmm or '00:00', lang)}"
    if lang == "hinglish":
        return f"{day_phrase(d, lang)} ko {clock(hhmm or '00:00', lang)}"
    return f"{day_phrase(d, lang)} को {clock(hhmm or '00:00', lang)}" if lang == "hi" else f"{day_phrase(d, lang)} {clock(hhmm or '00:00', lang)}"


def spaced(phone: str) -> str:
    d = re.sub(r"\D", "", phone)
    return f"{d[:5]} {d[5:]}" if len(d) == 10 else d


def human_when(opt: Optional[dict], lang: str, nights: Optional[int] = None) -> str:
    if not opt:
        return ""
    d = date.fromisoformat(opt["date"])
    return when_phrase(d, opt.get("time"), nights, lang)


# Every reply: key -> {language: template}. Hinglish is Roman Hindi (the app turns it into Devanagari for the voice).
T: Dict[str, Dict[str, str]] = {
    "ask_when": {
        "en": "Sure. Which day and time would you like?",
        "hi": "ज़रूर। आप किस दिन और किस समय आना चाहेंगे?",
        "mr": "नक्कीच. तुम्हाला कोणता दिवस आणि कोणती वेळ हवी आहे?",
        "hinglish": "Zaroor. Aap kis din aur kitne baje aana chahenge?"},
    "ask_date": {
        "en": "Which day would you like?", "hi": "आप किस दिन आना चाहेंगे?", "mr": "तुम्हाला कोणता दिवस हवा आहे?",
        "hinglish": "Aap kis din aana chahenge?"},
    "ask_time": {
        "en": "At what time on {day}?", "hi": "{day} को किस समय?", "mr": "{day} कोणत्या वेळी?", "hinglish": "{day} ko kitne baje?"},
    "free_ask": {
        "en": "{when} is free. Could you tell me {need}?",
        "hi": "{when} का समय खाली है। कृपया {need} बताइए।",
        "mr": "{when} ची वेळ मोकळी आहे. कृपया {need} सांगा.",
        "hinglish": "{when} ka time khaali hai. Kripya {need} bataiye."},
    "need_both": {"en": "your name and phone number", "hi": "आपका नाम और फ़ोन नंबर", "mr": "तुमचे नाव आणि फोन नंबर",
                  "hinglish": "aapka naam aur phone number"},
    "need_name": {"en": "your name", "hi": "आपका नाम", "mr": "तुमचे नाव", "hinglish": "aapka naam"},
    "need_phone": {"en": "your phone number", "hi": "आपका फ़ोन नंबर", "mr": "तुमचा फोन नंबर", "hinglish": "aapka phone number"},
    "readback": {
        "en": "Let me read that back: {summary}. Shall I book it?",
        "hi": "मैं पढ़कर बताती हूँ: {summary}। क्या मैं बुक करूँ?",
        "mr": "मी वाचून दाखवते: {summary}. मी बुक करू का?",
        "hinglish": "Main padhkar batati hoon: {summary}. Kya main book karoon?"},
    "summary": {
        "en": "{when} for {name}, phone {phone}",
        "hi": "{when}, {name} के नाम से, फ़ोन {phone}",
        "mr": "{when}, {name} यांच्या नावाने, फोन {phone}",
        "hinglish": "{when}, {name} ke naam se, phone {phone}"},
    "booked": {
        "en": "Done. Booking {id} is confirmed for {when}. Anything else I can help with?",
        "hi": "हो गया। बुकिंग {id} {when} के लिए पक्की हो गई है। और कुछ मदद चाहिए?",
        "mr": "झाले. बुकिंग {id} {when} साठी निश्चित झाली आहे. आणखी काही मदत हवी आहे का?",
        "hinglish": "Ho gaya. Booking {id} {when} ke liye pakki ho gayi hai. Aur kuch madad chahiye?"},
    "taken": {
        "en": "Sorry, {when} is already taken. {first} or {second}, which would suit you?",
        "hi": "माफ़ कीजिए, {when} का समय भरा हुआ है। {first} या {second}, कौन सा ठीक रहेगा?",
        "mr": "क्षमस्व, {when} ची वेळ भरलेली आहे. {first} किंवा {second}, कोणती चालेल?",
        "hinglish": "Maaf kijiye, {when} ka time bhara hua hai. {first} ya {second}, kaun sa theek rahega?"},
    "taken_one": {
        "en": "Sorry, {when} is not available. {first} is free, would that suit you?",
        "hi": "माफ़ कीजिए, {when} उपलब्ध नहीं है। {first} खाली है, क्या वह ठीक रहेगा?",
        "mr": "क्षमस्व, {when} उपलब्ध नाही. {first} मोकळी आहे, चालेल का?",
        "hinglish": "Maaf kijiye, {when} available nahi hai. {first} khaali hai, kya woh theek rahega?"},
    "closed": {
        "en": "Sorry, we cannot take {when}: {why}. Which other day or time would you like?",
        "hi": "माफ़ कीजिए, {when} नहीं हो सकता: {why}। आप कोई और दिन या समय बताइए।",
        "mr": "क्षमस्व, {when} शक्य नाही: {why}. कृपया दुसरा दिवस किंवा वेळ सांगा.",
        "hinglish": "Maaf kijiye, {when} nahi ho sakta: {why}. Koi aur din ya time bataiye."},
    "found_cancel": {
        "en": "I found booking {id} on {when} for {name}. Shall I cancel booking {id}?",
        "hi": "मुझे {name} के नाम की बुकिंग {id} {when} के लिए मिली। क्या मैं बुकिंग {id} रद्द करूँ?",
        "mr": "मला {name} यांच्या नावाची बुकिंग {id} {when} साठी सापडली. मी बुकिंग {id} रद्द करू का?",
        "hinglish": "Mujhe {name} ke naam ki booking {id} {when} ke liye mili. Kya main booking {id} radd karoon?"},
    "cancelled": {
        "en": "Booking {id} is cancelled. Anything else I can help with?",
        "hi": "बुकिंग {id} रद्द हो गई है। और कुछ मदद चाहिए?",
        "mr": "बुकिंग {id} रद्द झाली आहे. आणखी काही मदत हवी आहे का?",
        "hinglish": "Booking {id} radd ho gayi hai. Aur kuch madad chahiye?"},
    "no_booking": {
        "en": "I could not find an upcoming booking for that number. Could you say the number again?",
        "hi": "इस नंबर पर मुझे कोई आने वाली बुकिंग नहीं मिली। क्या आप नंबर फिर से बताएँगे?",
        "mr": "या नंबरवर मला पुढील बुकिंग सापडली नाही. तुम्ही नंबर पुन्हा सांगाल का?",
        "hinglish": "Is number par mujhe koi aane wali booking nahi mili. Kya aap number phir se bataenge?"},
    "ask_phone_cancel": {
        "en": "Of course. What phone number is the booking under?",
        "hi": "ज़रूर। बुकिंग किस फ़ोन नंबर पर है?",
        "mr": "नक्कीच. बुकिंग कोणत्या फोन नंबरवर आहे?",
        "hinglish": "Zaroor. Booking kis phone number par hai?"},
    "keep": {
        "en": "No problem, I will leave it as it is. Anything else?", "hi": "ठीक है, मैं इसे वैसे ही रहने देती हूँ। और कुछ?",
        "mr": "ठीक आहे, मी ते तसेच ठेवते. आणखी काही?", "hinglish": "Theek hai, main ise waise hi rehne deti hoon. Aur kuch?"},
    "welcome_back": {
        "en": "Welcome back, {name}! ", "hi": "आपका फिर से स्वागत है, {name}! ", "mr": "पुन्हा स्वागत, {name}! ",
        "hinglish": "Phir se swagat hai, {name}! "},
    "aadhaar": {
        "en": "I am sorry, I cannot take or note an Aadhaar number, and I do not need it for your booking. ",
        "hi": "माफ़ कीजिए, मैं आधार नंबर नहीं ले सकती और न ही नोट कर सकती हूँ, आपकी बुकिंग के लिए उसकी ज़रूरत भी नहीं है। ",
        "mr": "क्षमस्व, मी आधार नंबर घेऊ शकत नाही किंवा नोंदवू शकत नाही, आणि तुमच्या बुकिंगसाठी त्याची गरजही नाही. ",
        "hinglish": "Maaf kijiye, main Aadhaar number nahi le sakti aur note bhi nahi kar sakti, aapki booking ke liye uski zaroorat bhi nahi hai. "},
    "unsure": {
        "en": "Sorry, which detail should I change?", "hi": "माफ़ कीजिए, कौन सी जानकारी बदलनी है?",
        "mr": "क्षमस्व, कोणती माहिती बदलायची आहे?", "hinglish": "Maaf kijiye, kaun si jaankari badalni hai?"},
    "hello": {
        "en": "Hello, how can I help you?", "hi": "नमस्ते, मैं आपकी कैसे मदद कर सकती हूँ?",
        "mr": "नमस्कार, मी तुमची कशी मदत करू?", "hinglish": "Namaste, main aapki kaise madad kar sakti hoon?"},
    "failed": {
        "en": "Sorry, I could not do that: {why}. Could you tell me again what you would like?",
        "hi": "माफ़ कीजिए, मैं यह नहीं कर पाई: {why}। आप फिर से बताइए आपको क्या चाहिए?",
        "mr": "क्षमस्व, मला हे जमले नाही: {why}. तुम्हाला काय हवे ते पुन्हा सांगा.",
        "hinglish": "Maaf kijiye, main yeh nahi kar payi: {why}. Aap phir se bataiye aapko kya chahiye?"},
}


def say(key: str, lang: str, **kw: Any) -> str:
    return T[key][lang].format(**kw)


# ------------------------------------------------------------------------------------------------------- the policy

def tool(tool_name: str, /, **args: Any) -> str:
    return json.dumps({"tool": tool_name, "arguments": {k: v for k, v in args.items() if v is not None}}, ensure_ascii=False)


def result_of(results: List[Tuple[str, bool, Any]], name: str) -> Optional[Tuple[bool, Any]]:
    for n, ok, v in reversed(results):
        if n == name:
            return ok, v
    return None


def previous_readback(agents: List[str], f: Facts) -> bool:
    """Did the agent's last reply read this booking back with the name and phone and ask? (the same test the tool loop applies
    to the day and time, plus the name and number)"""
    if not agents or not f.date:
        return False
    try:
        from services import tools as tools_mod
    except ImportError:  # pragma: no cover
        from backend.services import tools as tools_mod
    last = agents[-1]
    t = None
    if f.time:
        t = datetime.strptime(f.time, "%H:%M").time()
    if not tools_mod._slot_mentioned(last, f.date, t):
        return False
    if f.phone and not speech.says_digits(last, f.phone, minimum=10):
        return False
    if f.name and fold(f.name.split()[0]) not in fold(last):
        return False
    return True


def roman(name: Optional[str]) -> str:
    try:
        from services import asr_scoring
    except ImportError:  # pragma: no cover
        from backend.services import asr_scoring
    return asr_scoring.transliterate(name or "").title()


def booking_args(f: Facts) -> dict:
    return {"date": f.date.isoformat() if f.date else None, "time": f.time if not f.nights else None, "nights": f.nights}


def decide(messages: List[dict], system: str = "") -> str:
    parts = split_messages(messages)
    callers, agents, results, now = parts["callers"], parts["agents"], parts["results"], parts["now"]
    if not callers:
        return "Hello, how can I help you?"
    f = read_facts(callers, agents, now)
    lang = f.language
    cur = callers[-1]
    prefix = ""
    last_agent = agents[-1] if agents else ""

    # a caller who asks the agent to take an Aadhaar number is told no, and helped with the rest
    if f.aadhaar_now or (contains_any(last_agent, ["आधार", "aadhaar"]) and not has_word(cur, YES) and f.aadhaar_ever
                         and contains_any(cur, ["नोट", "note", "लिख", "record", "रिकॉर्ड", "लिहा"])):
        prefix = say("aadhaar", lang)

    # ----- cancel
    if f.cancel or (contains_any(last_agent, ["cancel booking", "रद्द करूँ", "रद्द करू", "रद्द कर दूँ"]) and not f.date):
        found = result_of(results, "find_booking")
        canc = result_of(results, "cancel")
        if canc is not None:
            ok, v = canc
            if ok and isinstance(v, dict):
                return prefix + say("cancelled", lang, id=v.get("booking_id", ""))
            return prefix + say("failed", lang, why=str(v)[:120])
        if found is not None:
            ok, v = found
            rows = (v.get("bookings") if isinstance(v, dict) else None) or []
            if not ok or not rows:
                return prefix + say("no_booking", lang)
            b = rows[0]
            when = when_phrase(date.fromisoformat(b["date"]), b.get("time"), b.get("nights"), lang)
            return prefix + say("found_cancel", lang, id=b["booking_id"], when=when, name=b.get("name") or "")
        m = re.search(r"\bA\s?-?\s?(\d{2,4})\b", last_agent)
        if m and "?" in last_agent and has_word(cur, YES):
            return tool("cancel", booking_id="A" + m.group(1))
        if m and "?" in last_agent and (has_word(cur, NO) and not has_word(cur, YES)):
            return say("keep", lang)
        if f.phone:
            return tool("find_booking", phone=f.phone)
        return prefix + say("ask_phone_cancel", lang)

    # ----- book
    stay = bool(f.nights) or "hotel" in system[:1500].lower()
    found = result_of(results, "find_booking")
    # a returning caller: the phone alone finds the earlier booking, and with it the name (and the usual time)
    known_booking = None
    if found and found[0] and isinstance(found[1], dict) and found[1].get("bookings"):
        known_booking = found[1]["bookings"][0]
    if f.phone and not f.name and found is None and not result_of(results, "book"):
        return tool("find_booking", phone=f.phone)
    if known_booking:
        if not f.name and known_booking.get("name"):
            f.name = known_booking["name"]
            if not agents:
                prefix = say("welcome_back", lang, name=f.name) + prefix
        if not f.time and not stay and known_booking.get("time"):
            f.time = known_booking["time"]
    if not f.date:
        return prefix + say("ask_when", lang)
    if not stay and not f.time:
        return prefix + say("ask_time", lang, day=day_phrase(f.date, lang))
    when = when_phrase(f.date, f.time, f.nights if stay else None, lang)
    need = []
    if not f.name:
        need.append("name")
    if not f.phone:
        need.append("phone")
    check = result_of(results, "check_availability")
    book = result_of(results, "book")

    if book is not None:
        ok, v = book
        if ok and isinstance(v, dict) and v.get("booking_id"):
            return prefix + say("booked", lang, id=v["booking_id"], when=when)
        if isinstance(v, str) and "Please give a name" in v:
            # the booking store takes letters without vowel signs only: write the name in Roman letters, as a careful model would
            return tool("book", **booking_args(f), name=roman(f.name), phone=f.phone)
        if isinstance(v, str) and v.startswith("NOT DONE"):
            return prefix + say("readback", lang, summary=summary(f, when, lang))
        if isinstance(v, dict) and v.get("alternatives"):
            return prefix + offer(v, when, lang, f)
        return prefix + say("failed", lang, why=(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))[:120])

    if check is None and (f.changed_now or not previous_checked(agents)):
        return tool("check_availability", **booking_args(f))
    if check is not None:
        ok, v = check
        if not ok:
            return prefix + say("failed", lang, why=str(v)[:120])
        if not v.get("available"):
            return prefix + offer(v, when, lang, f)
        if need:
            key = "need_both" if len(need) == 2 else "need_" + need[0]
            return prefix + say("free_ask", lang, when=when, need=say(key, lang))
    if need:
        key = "need_both" if len(need) == 2 else "need_" + need[0]
        return prefix + say("free_ask", lang, when=when, need=say(key, lang))

    # name, phone, day and time are all known: read back, then book on a yes
    if f.aadhaar_now:
        return prefix + say("readback", lang, summary=summary(f, when, lang))
    if previous_readback(agents, f) and not f.corrected_now and not f.changed_now:
        if has_word(cur, YES) or contains_any(cur, YES[10:]):
            return tool("book", **booking_args(f), name=f.name, phone=f.phone)
        if has_word(cur, NO):
            return prefix + say("unsure", lang)
        return prefix + say("readback", lang, summary=summary(f, when, lang))
    # not read back yet (or something changed): the tool answers NOT DONE with the sentence when it has not been read back
    if tool_would_refuse(agents, f):
        return tool("book", **booking_args(f), name=f.name, phone=f.phone)
    return prefix + say("readback", lang, summary=summary(f, when, lang))


def previous_checked(agents: List[str]) -> bool:
    """Has an earlier reply already said the time is free (or offered other times)? Then it was checked."""
    return any(contains_any(a, ["free", "खाली", "मोकळी", "khaali", "taken", "भरल", "bhara", "read that back", "पढ़कर", "वाचून", "padhkar"]) for a in agents)


def tool_would_refuse(agents: List[str], f: Facts) -> bool:
    """True when the tool loop would answer NOT DONE: the last reply did not read the day and time back with a question."""
    if not agents or not f.date:
        return True
    try:
        from services import tools as tools_mod
    except ImportError:  # pragma: no cover
        from backend.services import tools as tools_mod
    t = datetime.strptime(f.time, "%H:%M").time() if f.time else None
    return not tools_mod._slot_mentioned(agents[-1], f.date, t)


def summary(f: Facts, when: str, lang: str) -> str:
    return say("summary", lang, when=when, name=f.name or "", phone=spaced(f.phone or ""))


def offer(v: dict, when: str, lang: str, f: Facts) -> str:
    before, after = v.get("before"), v.get("after")
    alts = v.get("alternatives") or [x for x in (before, after) if x]
    stay = bool(f.nights)
    if v.get("reason") in ("closed", "outside_hours", "past", "too_far", "too_soon") and not alts:
        return say("closed", lang, when=when, why=str((v.get("say") or {}).get("en", v.get("reason")))[:100])
    texts = [human_when(a, lang, f.nights if stay else None) for a in alts if a]
    if len(texts) >= 2:
        return say("taken", lang, when=when, first=texts[0], second=texts[1])
    if len(texts) == 1:
        return say("taken_one", lang, when=when, first=texts[0])
    return say("closed", lang, when=when, why=str(v.get("reason") or "not free"))


# ----------------------------------------------------------------------------------------------------- the server

def completion(messages: List[dict], json_mode: bool = False) -> str:
    system = msg_text(messages[0]) if messages else ""
    if json_mode:
        return "{}"
    if "check_availability" not in system:
        return "Okay."
    return decide(messages, system)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # health and a probe of the policy
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self._send(200, json.dumps({"data": [{"id": "theatre-scripted"}]}).encode())
        else:
            self._send(200, b'{"ok": true, "model": "theatre-scripted"}')

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, b'{"error": {"message": "bad json"}}')
        messages = body.get("messages") or []
        json_mode = bool(body.get("response_format"))
        try:
            text = completion(messages, json_mode)
        except Exception as e:  # noqa: BLE001 - the server must answer; the failure is visible in the reply
            text = f"Sorry, the scripted model failed: {type(e).__name__}"
        if LOG_PATH:
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": time.time(), "messages": messages, "reply": text}, ensure_ascii=False) + "\n")
        usage = {"prompt_tokens": 1, "completion_tokens": max(1, len(text) // 4), "total_tokens": 2}
        if body.get("stream"):
            chunks = re.findall(r"\S+\s*", text) or [text]
            out = b""
            for c in chunks:
                out += ("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": c}}]}, ensure_ascii=False) + "\n\n").encode()
            out += ("data: " + json.dumps({"choices": [], "usage": usage}) + "\n\n").encode() + b"data: [DONE]\n\n"
            self._send(200, out, "text/event-stream")
        else:
            self._send(200, json.dumps({"id": "theatre", "object": "chat.completion", "model": "theatre-scripted",
                                        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                                     "finish_reason": "stop"}], "usage": usage},
                                       ensure_ascii=False).encode())

    do_PUT = do_POST


def serve(port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main(argv: Optional[List[str]] = None) -> int:
    global LOG_PATH
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--log", help="append every request and reply to this JSON-lines file")
    a = ap.parse_args(argv)
    LOG_PATH = a.log
    server = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"scripted model on http://{a.host}:{a.port}/v1  (LLM_BASE_URL=http://{a.host}:{a.port}/v1 LLM_API_KEY=theatre LLM_MODEL=theatre-scripted)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
