"""Digit repair and keypad entry: a phone number is never thrown away because it arrived in pieces.

Spoken ten-digit numbers are the field speech recognition gets wrong most often on a phone line (docs/STRESS_REPORT.md),
so the caller details form (services/caller_memory.py) keeps two extra keys next to `phone`:

  phone_partial   the digits heard so far when they are not yet a whole mobile number ("987654321": 9 of 10)
  phone_tries     how many phone-number answers in a row did not give a whole number

`repair(form, caller_text, agent_text)` runs after the rules have read the caller's words (turn_pipeline.turn_caller_memory):

  * a whole number in the caller's words wins and clears the repair keys;
  * digits said while a number is being given are kept as a partial number; the next piece is joined on ("98765" then
    "43210"), or replaces the end of what was heard ("987654321", then "say the last digits again": "43210");
  * after the agent read a number back, a short run of digits replaces only the end of it ("nahi, last wale 3 2 1 0"),
    or its start when the caller names the start ("pehle", "first", "सुरुवातीचे"), and only when the result is a valid
    Indian mobile number;
  * a time ("6 30 baje"), a short answer ("2") or digits nobody asked for ("room 40512") change nothing.

On a phone call the caller's own number (the caller ID) is offered first, so a caller who wants it never dictates ten
digits: the phone router puts it in the form as `phone_offer` (`offer_own_number`), `prompt_note` tells the model to ask
"Shall I book it on this number, ending 3 2 1 0?", and `repair` reads the answer in code:

  phone_offer     the number the caller is calling from, kept until it is accepted, declined or another number is given
  phone_offer_no  "1" once the caller said no: the model then asks them to say the number or type it, ending with #

A yes (haan, ho, yes, same number, isi number pe) to that question makes it the booking's phone; the read-back and the
spoken yes before booking are unchanged.

`prompt_note(form, keypad)` is the line the language model gets while the number is incomplete: what was heard, how many
digits, ask only for the rest. After KEYPAD_AFTER tries on a phone call it also says to offer the keypad.

`KeypadEntry` collects the digits a caller types once the agent has asked for the keypad (`asks_for_keypad`): 0 to 9 are
digits (0 does not ask for a person while typing), # submits, * asks for a person, and ten digits of a mobile number
submit on their own. The phone router (routers/phone.py) turns the submitted number into the caller's next turn.

Pure functions, no I/O: never logs a digit."""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

try:
    from services import spoken_digits, when
except ImportError:  # pragma: no cover
    from backend.services import spoken_digits, when

PARTIAL = "phone_partial"
TRIES = "phone_tries"
OFFER = "phone_offer"
OFFER_NO = "phone_offer_no"
KEYS = (PARTIAL, TRIES, OFFER, OFFER_NO)
KEYPAD_AFTER = 2
MIN_DIGITS = 3          # fewer is a party size, an age or a slot number, never a piece of a phone number
MOBILE = 10

# The agent or the caller is talking about a phone number (the agent's read-back says "phone number ending ...").
_PHONE_WORDS = re.compile(
    r"\b(?:phone|mobile|contact|number|nambar|digits?|ending)\b|नंबर|नम्बर|मोबाइल|मोबाईल|फ़ोन|फोन|अंक|आकडे|क्रमांक",
    re.IGNORECASE)
# The caller names the start of the number ("the first five", "pehle paanch", "सुरुवातीचे").
_HEAD = re.compile(
    r"\b(?:first|starting|start|beginning|pehle|pehla|pehli|pahle|pahla|shuru|shuruat|suruvat|suruvatiche)\b|"
    r"पहले|पहला|पहली|शुरू|शुरुआत|सुरुवात|सुरुवातीचे|पहिले|पहिला",
    re.IGNORECASE)
# A time said with its hour and minutes apart ("6 30 baje") is not read by when.time_mentions; these words mark a time.
_TIME_WORDS = re.compile(
    r"\b(?:baje|bajey|bajkar|vajta|vajata|am|pm|a\.m|p\.m|o'?clock|subah|sakali|shaam|sham|dopahar|dupari|raat|ratri|"
    r"morning|afternoon|evening|night)\b|बजे|बजकर|वाजता|वाजून|सुबह|सकाळी|शाम|संध्याकाळी|दोपहर|दुपारी|रात",
    re.IGNORECASE)
# The agent's question about the caller's own number: "this number", "same number", "isi number", "इसी नंबर", "याच नंबर".
_THIS_NUMBER = re.compile(
    r"\b(?:this|same|current)\s+(?:phone\s+|mobile\s+)?number\b|\b(?:isi|issi|yahi)\s+(?:number|nambar)\b|"
    r"\byach\s+(?:number|nambar)\b|इसी\s*(?:नंबर|नम्बर)|यही\s*(?:नंबर|नम्बर)|याच\s*(?:नंबर|क्रमांक)|हाच\s*(?:नंबर|क्रमांक)",
    re.IGNORECASE)
_OFFER_YES = re.compile(
    r"\b(?:yes|yeah|yep|yup|ok|okay|sure|correct|right|haan|haa|han|ha|hanji|haanji|ji|ho|hoy|theek|thik|chalega|"
    r"same|wahi|wohi|yahi|isi|issi|yach|barobar)\b|हाँ|हां|\bहा\b|जी|ठीक|चलेगा|वही|यही|इसी|\bहो\b|होय|बरोबर|हाच|याच",
    re.IGNORECASE)
_OFFER_NO = re.compile(
    r"\b(?:no|nope|nahi|nahin|nai|na|nako|nahi|different|another|other|dusra|dusre|doosra|dusri|vegla)\b|"
    r"नहीं|नही|\bना\b|दूसरा|दूसरे|दूसरी|नको|नाही|वेगळा|दुसरा|दुसरे",
    re.IGNORECASE)
_KEYPAD = re.compile(r"key\s?pad|dial\s?pad|\bhash\b|कीपैड|की-पैड|कीपॅड|डायल\s?पैड|डायलपॅड|#", re.IGNORECASE)


def _digits_said(text: str) -> str:
    """The longest run of digits in `text` (numerals, digit words or Devanagari digits), "" when none."""
    runs = ["".join(d for d, _ in run) for run in spoken_digits.digit_token_runs(text)]
    return max(runs, key=len, default="")


def _valid(digits: str) -> Optional[str]:
    return spoken_digits._valid_mobile(digits)   # noqa: SLF001 - the one definition of a valid mobile number


def _tries(form: Dict[str, str]) -> int:
    try:
        return int(form.get(TRIES) or 0)
    except ValueError:
        return 0


def _done(form: Dict[str, str], phone: str) -> Dict[str, str]:
    form["phone"] = phone
    for key in KEYS:
        form.pop(key, None)
    return form


def _missed(form: Dict[str, str], partial: Optional[str]) -> Dict[str, str]:
    if partial:
        form[PARTIAL] = partial
    form[TRIES] = str(_tries(form) + 1)
    return form


def offers_own_number(agent_text: Optional[str], offer: Optional[str] = None) -> bool:
    """The agent's last reply asks whether to use the number the caller is calling from: "this number" (in any of the
    three languages), or the last four digits of `offer` said as a phone number ("ending 3 2 1 0")."""
    text = str(agent_text or "")
    if _THIS_NUMBER.search(text):
        return True
    if offer and len(offer) >= 4 and _PHONE_WORDS.search(text):
        runs = ["".join(d for d, _ in run) for run in spoken_digits.digit_token_runs(text)]
        return any(r.endswith(offer[-4:]) for r in runs)
    return False


def own_number_answer(caller_text: Optional[str]) -> Optional[bool]:
    """The caller's answer to "Shall I book it on this number?": True (yes), False (no, or another number), None (not an
    answer: digits, a question, something else)."""
    text = str(caller_text or "")
    if _OFFER_NO.search(text):
        return False
    if _OFFER_YES.search(text) and len(_digits_said(text)) < MIN_DIGITS:
        return True
    return None


def offer_own_number(form: Optional[Dict[str, str]], number: Optional[str]) -> Dict[str, str]:
    """`form` with the caller's own number (the caller ID) offered, unless a phone number is already known or the caller
    already declined it. A new dict. A number that is not a valid Indian mobile number is not offered."""
    out = dict(form or {})
    found = _valid(re.sub(r"\D", "", str(number or "")))
    if found and not out.get("phone") and not out.get(OFFER_NO):
        out[OFFER] = found
    return out


def repair(form: Optional[Dict[str, str]], caller_text: Optional[str], agent_text: Optional[str] = None) -> Dict[str, str]:
    """`form` with the phone number repaired from what the caller just said (see the module docstring). A new dict;
    `form` itself is not changed. Unchanged when the turn has nothing to do with a phone number."""
    out = dict(form or {})
    text = str(caller_text or "")
    whole = spoken_digits.find_mobile(text)
    if whole:
        return _done(out, whole)
    offer = out.get(OFFER)
    if offer and not out.get("phone") and offers_own_number(agent_text, offer):
        answer = own_number_answer(text)
        if answer is True:
            return _done(out, offer)                     # "haan, isi number pe": no digits dictated
        if answer is False and len(_digits_said(text)) < MIN_DIGITS:
            out.pop(OFFER, None)
            out[OFFER_NO] = "1"                          # ask for the number, and offer the keypad at once
            return out
    said = _digits_said(text)
    if len(said) < MIN_DIGITS or len(said) > 13:
        return out
    partial, phone = out.get(PARTIAL) or "", out.get("phone") or ""
    talking_phone = bool(partial) or bool(_PHONE_WORDS.search(str(agent_text or ""))) or bool(_PHONE_WORDS.search(text))
    if not talking_phone:
        return out
    if (when.time_mentions(text) or _TIME_WORDS.search(text)) and len(said) < 5:
        return out                                    # "6 30 baje" is a time, not part of a number
    head = bool(_HEAD.search(text))
    if partial:
        options = [partial + said]
        if len(said) < MOBILE:
            options.append(said + partial[len(said):] if head else partial[:MOBILE - len(said)] + said)
        for candidate in options:
            found = _valid(candidate)
            if found:
                return _done(out, found)
        joined = partial + said
        return _missed(out, joined if len(joined) < MOBILE else said if len(said) < MOBILE else None)
    if phone:
        if len(said) >= MOBILE:
            return out                                # a whole invalid number does not replace a valid one
        candidate = said + phone[len(said):] if head else phone[:MOBILE - len(said)] + said
        found = _valid(candidate)
        return _done(out, found) if found else out
    return _missed(out, said if len(said) < MOBILE else None)


def _grouped(digits: str) -> str:
    """"987654321" -> "98765 4321": five digits, then the rest, as people read a mobile number."""
    return digits if len(digits) <= 5 else f"{digits[:5]} {digits[5:]}"


def prompt_note(form: Optional[Dict[str, str]], keypad: bool = False) -> str:
    """The line the language model gets while the phone number is incomplete ("" once it is whole, or before any of it
    was given). `keypad` (a phone call) adds the keypad offer after KEYPAD_AFTER tries."""
    form = form or {}
    if form.get("phone"):
        return ""
    partial, tries = form.get(PARTIAL) or "", _tries(form)
    offer = form.get(OFFER)
    if offer and not partial and not tries:
        tail = " ".join(offer[-4:])
        return (f"(The caller is calling from a mobile number ending {tail}. When you need their phone number, do not ask "
                f"them to say it: first ask, in the caller's language, whether to use this number, e.g. \"Shall I book it "
                f"on this number, ending {tail}?\" Use it only after they say yes.)")
    if form.get(OFFER_NO) and not partial and not tries:
        return ("(The caller does not want the number they are calling from. Ask them to say their phone number"
                + (", or type it on the phone keypad and press the hash key, #." if keypad else ".") + ")")
    if not partial and not tries:
        return ""
    parts = []
    if partial:
        parts.append(f"(Phone number not complete yet: heard {_grouped(partial)}, {len(partial)} of {MOBILE} digits. "
                     "Ask only for the missing digits, not the whole number again. Once it is complete, read the whole "
                     "number back.)")
    else:
        parts.append("(The phone number has not been heard clearly yet. Ask for it in two parts: the first five digits, "
                     "then the last five.)")
    if keypad and tries >= KEYPAD_AFTER:
        parts.append("(It is still not clear: ask the caller to type the number on the phone keypad and press the hash "
                     "key, #.)")
    return " ".join(parts)


def asks_for_keypad(agent_text: Optional[str]) -> bool:
    """The agent's last reply asks the caller to type on the keypad (English, Hindi or Marathi)."""
    return bool(_KEYPAD.search(str(agent_text or "")))


class KeypadEntry:
    """The digits of one number being typed. `press(key)` returns (action, value):

      ("wait", None)        a digit was kept, more are expected
      ("submit", "98765 43210")   the number is complete (# or ten digits of a mobile number), grouped for speech
      ("human", None)       * : the caller wants a person
      ("reset", None)       # with 1 or 2 digits: start again
      ("ignore", None)      any other key, or # right after a number submitted itself at ten digits
    """

    def __init__(self) -> None:
        self._digits = ""

    def press(self, key: str) -> Tuple[str, Optional[str]]:
        key = str(key or "").strip()
        if key == "*":
            self._digits = ""
            return "human", None
        if key == "#":
            digits, self._digits = self._digits, ""
            if not digits:
                return "ignore", None         # the # people press after a number that already went
            if len(digits) < MIN_DIGITS:
                return "reset", None
            return "submit", _grouped(_valid(digits) or digits)
        if len(key) != 1 or not key.isdigit():
            return "ignore", None
        self._digits += key
        found = _valid(self._digits)
        prefixed = self._digits.startswith(("91", "0"))
        if found and (not prefixed or len(self._digits) > MOBILE) or len(self._digits) >= 13:
            digits, self._digits = self._digits, ""
            return "submit", _grouped(_valid(digits) or digits)
        return "wait", None
