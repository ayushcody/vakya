"""What is the caller about to say? Read the front desk's last reply (English, Hindi, Marathi, and Hindi or Marathi written in
Latin letters) and decide whether it asked for a NAME, a DAY or TIME, a PHONE NUMBER or other digits, a YES or NO (the
read-back of a booking), or nothing in particular (open speech).

The answer steers what Prisma is told for the next utterance (services/hearing.py: `topic`, extra `keyterms`, `speaker_names`)
and what the desk's live screen says ("Listening for: a name"). It is a phrase list, not a model: it is fast, it cannot fail a
turn, and every phrase it knows is below, in `PHRASES`.

    detect("May I have your name?")                       -> {"kinds": ["name"], "primary": "name", ...}
    detect("आपका मोबाइल नंबर और नाम बताइए")               -> kinds ["digits", "name"] (in the order they are asked)
    detect("Sunita, Thursday 5 pm. Is that right?")        -> kinds ["yes_no"]
    detect("How can I help you?")                          -> kinds [] / primary "open"

The last sentence that asks for something wins; a read-back question ends in a yes or no, so it beats the details it repeats."""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

KINDS = ("name", "when", "digits", "yes_no")
OPEN = "open"

_L = r"(?<![\wऀ-ॿ])"        # a word starts here (Devanagari matras are not \w, so \b is not enough)
_R = r"(?![\wऀ-ॿ])"

# kind -> language -> phrases (regular expressions, case-insensitive). "yes_no" has strong phrases (a read-back or a
# confirmation question, decisive on their own) and weak ones (a bare tag such as "right?" or "ठीक है?", which only counts when
# the sentence is not itself asking for a detail).
PHRASES: Dict[str, Dict[str, List[str]]] = {
    "yes_no": {
        "en": [r"\b(is|are|does|do) (that|this|it|all of that|everything) (right|correct|okay|ok|fine|good|alright)\b",
               r"\b(shall|should) i (book|confirm|go ahead|cancel|make|proceed|note)\b",
               r"\bwould you like me to (book|confirm|go ahead|cancel|proceed)\b",
               r"\bdo you (want|wish) (me )?to (confirm|book|cancel|go ahead|proceed)\b",
               r"\bcan i confirm\b", r"\bconfirm (this|that|it)\b", r"\bsound good\b", r"\bdoes that work\b",
               r"\bis that (all|everything)\b"],
        "hi": [r"सही\s*(है|रहेगा|होगा)", r"क्या\s*(यह|ये|ये सब)\s*(सही|ठीक)", r"कन्फ़?र्म", r"पुष्टि",
               r"(बुक|कैंसिल|रद्द|नोट)\s*कर\s*(दूँ|दूं|दें|दूँगी|दूंगा)", r"\b(sahi|theek|thik)\s*(hai|rahega)\b",
               r"\bbook\s*kar\s*(doon|dun|du)\b"],
        "mr": [r"बरोबर", r"(करू|करु|करूया)\s*का", r"ठीक\s*आहे\s*का", r"चालेल\s*का", r"योग्य\s*आहे\s*का",
               r"पक्के\s*करू", r"\b(barobar|karu\s*ka|chalel\s*ka)\b"],
    },
    "yes_no_weak": {
        "en": [r"\b(right|correct|okay|ok|alright|fine|yes)\s*[?？]"],
        "hi": [rf"{_L}ठीक\s*है\s*[?？]", rf"{_L}(ना|न)\s*[?？]"],
        "mr": [r"(आहे|आहेत|होतं|होते)\s*(ना|का)\s*[?？]"],
    },
    "digits": {
        "en": [r"\b(mobile|phone|contact|cell|whatsapp|telephone|alternate)\s*(number|no\b|num\b)", r"\bnumber\b(?!\s+of\b)",
               r"\bdigits?\b", r"\bpin\s*code\b", r"\b(reference|booking|membership|room|order|ticket)\s*(number|id|no\b)",
               r"\b(call|reach)\s*(you\s*)?(back\s*)?(on|at)\b"],
        "hi": [r"(मोबाइल|मोबाईल|फोन|फ़ोन|संपर्क|कॉन्टैक्ट|व्हाट्सएप|व्हाट्सऐप)\s*(नंबर|नम्बर)", r"नंबर", r"नम्बर", r"अंक",
               r"पिन\s*कोड", r"\b(mobile|phone|contact)\s*(number|no|nambar)\b", r"\bnambar\b"],
        "mr": [r"क्रमांक", r"(मोबाईल|मोबाइल|फोन|संपर्क)\s*(नंबर|क्रमांक)", r"आकडे", r"नंबर"],
    },
    "name": {
        "en": [r"\b(your|the|their|patient'?s?|guest'?s?|customer'?s?|student'?s?|member'?s?|caller'?s?)\s+(full |first |last |good |complete )?name\b",
               r"\bname\s+(please|for the (booking|appointment|reservation|visit))\b", r"\bmay i (have|know|get)\s+(your|the) name\b",
               r"\bwho(\s+is|'s|\s+am i)\s+(this|it for|speaking|the (booking|appointment|reservation|visit|table|room) for)\b",
               r"\bunder what name\b", r"\bwhat name\b", r"\bwhat (should|can|do) i call you\b", r"\bfor whom\b"],
        "hi": [r"आपका\s*(पूरा |शुभ )?नाम", r"(पेशेंट|मरीज़?|ग्राहक|मेहमान|गेस्ट|स्टूडेंट)\s*का\s*(पूरा )?नाम",
               r"नाम\s*(क्या|बताइए|बताएं|बताइये|बताओ|लिखवाइए)", r"किसके\s*नाम", r"किस\s*नाम\s*से", r"किसके\s*लिए",
               r"\b(aapka|apka)\s*naam\b", r"\bnaam\s*(kya|bataiye|batayiye)\b", r"\bkiske\s*naam\b"],
        "mr": [r"(तुमचे|तुमचं|तुमचा|आपले|आपलं)\s*(पूर्ण |संपूर्ण )?नाव", r"नाव\s*(काय|सांगा|सांगाल|सांगू शकाल)",
               r"कोणाच्या\s*नावाने", r"कोणाचे\s*नाव", r"कोणासाठी", r"\btumche\s*nav\b", r"\bnaav\s*(kay|sanga)\b"],
    },
    "when": {
        "en": [r"\b(which|what)\s+(day|date|time|slot|morning|evening|afternoon)\b", r"\bwhen\s+(would|do|can|should|are|will|is)\b",
               r"\b(date|day|time)\s+(and|or)\s+(date|day|time)\b", r"\b(preferred|suitable|convenient|best)\s+(day|date|time|slot)\b",
               r"\bwhat time\b", r"\bsuits you\b"],
        "hi": [rf"{_L}कब{_R}", r"कौन\s*(सा|सी)\s*(दिन|तारीख|तारिख|समय|वक़्त|वक्त|टाइम|स्लॉट)",
               r"किस\s*(दिन|तारीख|समय|वक़्त|वक्त|टाइम)", r"कितने\s*बजे", r"(दिन|तारीख|समय|टाइम)\s*(बताइए|बताएं|बताइये|बताओ)",
               r"\bkab\b", r"\bkis\s*din\b", r"\bkitne\s*baje\b", r"\bkaun\s*sa\s*din\b"],
        "mr": [rf"{_L}कधी{_R}", rf"{_L}केव्हा{_R}", r"कोणत्या\s*(दिवशी|तारखेला|वेळी|वेळेला|वेळेस|दिवस|तारीख|वेळ)",
               r"कोणता\s*(दिवस|वेळ)", r"कोणती\s*(तारीख|वेळ)", r"किती\s*वाजता", r"(वेळ|दिवस|तारीख)\s*सांगा",
               r"\bkadhi\b", r"\bkiti\s*vajta\b", r"\bkonta\s*divas\b"],
    },
}

# A sentence that asks for a detail (so a trailing "right?" is a tag on that request, not a read-back).
_ASKS = re.compile(r"\b(what|which|when|who|please|tell|give|say|share|provide|may i have|could you|can you|would you)\b"
                   r"|बताइए|बताएं|बताइये|बताओ|कृपया|कौन|कब|कितने|सांगा|सांगाल|काय|कधी|कोणत्या|क्या\s+(है|नाम|नंबर)",
                   re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.?!।？])\s+|\n+")

_COMPILED: Dict[str, List[Tuple[str, "re.Pattern[str]"]]] = {
    kind: [(lang, re.compile(p, re.IGNORECASE)) for lang, ps in langs.items() for p in ps]
    for kind, langs in PHRASES.items()
}


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE.split(unicodedata.normalize("NFC", text or "")) if s and s.strip()]


def _first(kind: str, sentence: str) -> Optional[Tuple[int, str, str]]:
    """(position, language, matched text) of the earliest phrase of `kind` in `sentence`."""
    best: Optional[Tuple[int, str, str]] = None
    for lang, rx in _COMPILED[kind]:
        m = rx.search(sentence)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), lang, m.group(0))
    return best


def classify_sentence(sentence: str) -> List[Tuple[str, str, str]]:
    """[(kind, language, matched phrase)] for one sentence, in the order asked. A confirmation question stands alone."""
    strong = _first("yes_no", sentence)
    if strong:
        return [("yes_no", strong[1], strong[2])]
    found = []
    for kind in ("name", "when", "digits"):
        hit = _first(kind, sentence)
        if hit:
            found.append((hit[0], kind, hit[1], hit[2]))
    if not found:
        weak = _first("yes_no_weak", sentence)
        return [("yes_no", weak[1], weak[2])] if weak else []
    if _first("yes_no_weak", sentence) and not _ASKS.search(sentence):
        weak = _first("yes_no_weak", sentence)
        return [("yes_no", weak[1], weak[2])]
    found.sort()
    return [(k, lang, phrase) for _pos, k, lang, phrase in found]


def detect(text: Optional[str]) -> dict:
    """What the reply `text` asked the caller for: {"kinds": [...], "primary": kind or "open", "language": "en"|"hi"|"mr"|None,
    "matched": [phrases]}. The last sentence that asks for something decides; with none, the speech is open."""
    for sentence in reversed(_sentences(text or "")):
        hits = classify_sentence(sentence)
        if hits:
            kinds: List[str] = []
            for k, _l, _p in hits:
                if k not in kinds:
                    kinds.append(k)
            return {"kinds": kinds, "primary": kinds[0], "language": hits[0][1], "matched": [p for _k, _l, p in hits]}
    return {"kinds": [], "primary": OPEN, "language": None, "matched": []}


# ------------------------------------------------------------------------------------------ what each expectation adds

WEEKDAYS = {
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "hi": ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
    "mr": ["सोमवार", "मंगळवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
}
MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
           "December"],
    "hi": ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
    "mr": ["जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून", "जुलै", "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर", "नोव्हेंबर",
           "डिसेंबर"],
}
DAY_WORDS = ["today", "tomorrow", "आज", "कल", "परसों", "उद्या", "परवा", "सुबह", "दोपहर", "शाम", "सकाळी", "संध्याकाळी",
             "morning", "afternoon", "evening"]
YES_NO_WORDS = ["हाँ", "हां", "नहीं", "हो", "होय", "नाही", "बरोबर", "सही", "yes", "no", "correct"]
DIGIT_WORDS = ["शून्य", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ", "दोन", "पाच", "सहा", "नऊ",
               "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "double", "triple"]

TERMS: Dict[str, List[str]] = {
    "when": [w for lang in ("en", "hi", "mr") for w in WEEKDAYS[lang] + MONTHS[lang]] + DAY_WORDS,
    "yes_no": YES_NO_WORDS,
    "digits": DIGIT_WORDS,
    "name": [],
}

LABELS = {"name": "a name", "when": "a day or time", "digits": "digits", "yes_no": "yes or no"}

TOPICS = {
    "name": "The caller is saying their name to {desk}.",
    "when": "The caller is saying a day, a date or a time of day for a booking at {desk}.",
    "digits": "The caller is saying a phone number or other digits, one digit at a time, to {desk}.",
    "yes_no": "The caller is answering yes or no to a read-back of their booking at {desk}.",
    OPEN: "The caller is talking to {desk}.",
}


def terms_for(kinds: List[str]) -> List[str]:
    """Extra keyterms for what is expected (de-duplicated, in order)."""
    out: List[str] = []
    for k in kinds:
        for t in TERMS.get(k, []):
            if t not in out:
                out.append(t)
    return out


def label_for(kinds: List[str]) -> str:
    """"a name", "a name and digits", "yes or no" ..."""
    return " and ".join(LABELS[k] for k in kinds if k in LABELS)


def topic_for(kinds: List[str], desk: str, names: Optional[List[str]] = None, desk_names: Optional[List[str]] = None) -> str:
    """The `topic` text for Prisma (at most 1,600 characters): what the caller is saying, to which kind of desk, and the
    names the desk uses."""
    parts = [TOPICS[k].format(desk=desk) for k in (kinds or [OPEN]) if k in TOPICS] or [TOPICS[OPEN].format(desk=desk)]
    text = " ".join(parts)
    if names:
        text += " The caller may be called " + ", ".join(names[:4]) + "."
    if desk_names:
        text += " Names used at this desk: " + ", ".join(desk_names[:12]) + "."
    return text[:1600]
