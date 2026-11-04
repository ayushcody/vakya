"""Gap detection: does an agent's reply say it could not answer from its documents ("I don't have that information")?

`is_gap_reply` recognises the guardrail prompt's own wording in several languages; the Calls page uses it to mark a call
"couldn't answer" and the caller profile to flag a follow-up. `redact` strips identifiers (emails, phone numbers, Aadhaar-like
groups, long digit runs) from a question before it is shown or kept. No web framework, no model."""
import re
from typing import Tuple

MAX_TEXT_CHARS = 300

# -- gap detection ---------------------------------------------------------------------------------------------------

# Lower-case, curly apostrophes already straightened. Substring match; kept specific enough not to fire on answers.
GAP_PHRASES: Tuple[str, ...] = (
    # English (the guardrail prompt's own wording first)
    "don't have that information", "do not have that information", "don't have any information",
    "do not have any information", "don't have information", "i don't have the details", "not in my documents",
    "not in the documents", "not in my knowledge", "couldn't find", "could not find", "can't find", "cannot find",
    "unable to find", "i'm not sure about that", "i am not sure about that", "i don't know", "i do not know",
    "no information about", "not something i have", "outside what i know", "connect you with a member of staff",
    "member of our staff", "member of staff can",
    # Hindi (Devanagari)
    "मेरे पास यह जानकारी नहीं", "मेरे पास इसकी जानकारी नहीं", "मेरे पास जानकारी नहीं", "जानकारी उपलब्ध नहीं",
    "जानकारी नहीं है", "मुझे नहीं पता", "मुझे पता नहीं", "पता नहीं है", "मुझे यह नहीं पता", "मुझे जानकारी नहीं",
    "मुझे इसकी जानकारी नहीं", "स्टाफ के सदस्य", "कर्मचारी से जोड़",
    # Hinglish / romanised Hindi
    "mere paas yeh jaankari nahi", "mere paas ye jaankari nahi", "mere paas jaankari nahi",
    "mere paas is baare mein jaankari nahi", "mere paas information nahi", "jaankari nahi hai", "jankari nahi hai",
    "jaankari uplabdh nahi", "mujhe nahi pata", "mujhe pata nahi", "pata nahi hai", "mujhe iski jaankari nahi",
    "staff se baat", "staff member se",
    # Marathi / Gujarati / Bengali / Tamil / Telugu (short, common refusals)
    "माझ्याकडे ही माहिती नाही", "मला माहित नाही", "મારી પાસે આ માહિતી નથી", "আমার কাছে এই তথ্য নেই",
    "என்னிடம் அந்த தகவல் இல்லை", "నా దగ్గర ఆ సమాచారం లేదు",
    # Spanish / French / German / Portuguese
    "no tengo esa información", "no tengo información", "je n'ai pas cette information", "je ne dispose pas",
    "ich habe keine informationen", "ich habe diese informationen nicht", "não tenho essa informação",
)

_APOS = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'"})
_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", (text or "").translate(_APOS).lower()).strip()


def is_gap_reply(reply: str) -> bool:
    """True when the agent's reply says it does not have the answer (or hands the caller to a human for lack of one)."""
    t = _norm(reply)
    return bool(t) and any(p in t for p in GAP_PHRASES)


# -- redaction -------------------------------------------------------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_AADHAAR = re.compile(r"(?<!\d)\d{4}[ -]\d{4}[ -]\d{4}(?!\d)")
_PHONE_IN = re.compile(r"(?<![\w])(?:\+\s?91[\s-]?|91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
_LONG_DIGITS = re.compile(r"(?<!\d)(?:\d[ -]?){5,}\d(?!\d)")


def redact(text: str) -> str:
    """Strip emails, Indian phone numbers (10 digits, +91), Aadhaar-like 12-digit groups and long digit runs."""
    t = (text or "")[:2000]
    t = _EMAIL.sub("[email]", t)
    t = _AADHAAR.sub("[id]", t)
    t = _PHONE_IN.sub("[phone]", t)
    t = _LONG_DIGITS.sub("[number]", t)
    t = _WS.sub(" ", t).strip()
    return t[:MAX_TEXT_CHARS]
