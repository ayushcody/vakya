"""Identity numbers a caller says are never kept: Aadhaar-style numbers, PAN, card numbers and one-time passwords.

`redact_ids(text)` returns the text with each of them replaced by "[ID number removed]". It is applied where a caller's
words (and the agent's reply, which could repeat them) are stored or sent out: the stored turn, the call events, the
kept utterance of the booking evidence and the logs. Phone numbers (10 digits, with or without +91) are not touched here;
they are masked to their last four digits for display as before (services/call_events.mask_numbers).

What counts:
  * a run of digits, written or said (numerals, Devanagari digits, English / Hindi / Hinglish digit words, "double five"),
    with spaces, commas or hyphens between groups, that has exactly 12 digits (an Aadhaar number; a 12-digit run that is
    91 and a mobile number is the phone number and stays), or 13 to 19 digits that pass the Luhn check (a card number);
  * a PAN: five letters, four digits and a letter ("ABCDE1234F", "abcde1234f" written together, or spaced capitals);
  * the digits that follow the word OTP (or "one time password", ओटीपी): 4 to 8 digits.

A sentence end (. ! ? । ;) ends a run, as in services/spoken_digits.py, whose digit words are used here."""
from __future__ import annotations

import re
from typing import List, Tuple

try:
    from services import spoken_digits as sd
except ImportError:  # pragma: no cover
    from backend.services import spoken_digits as sd

MARKER = "[ID number removed]"

_DEVA = str.maketrans("०१२३४५६७८९", "0123456789")
_TOKEN = re.compile(r"[\wऀ-ॿ]+")
_GAP = re.compile(r"^[ \t,\-–]*$")
_OTP_WORD = re.compile(r"\botp\b|one[\s-]?time[\s-]?(?:password|code|pin)|ओटीपी|ओ\.?टी\.?पी", re.IGNORECASE)
_OTP_FILLER = {"is", "was", "number", "no", "code", "hai", "hain", "he", "है", "हैं", "आहे", "ka", "ki", "ko", "का", "की",
               "the", "my", "mera", "meri", "मेरा", "मेरी", "माझा", "आहे", "to", "toh", "तो", ":", "-", "="}
_PAN_COMPACT = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{5}\d{4}[A-Za-z](?![A-Za-z0-9])")
_PAN_UPPER_SPACED = re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}[ -]?\d{4}[ -]?[A-Z](?![A-Za-z0-9])")
_PAN_LETTERS = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z][ -]){4}[A-Za-z][ -](?:\d[ -]){3}\d[ -][A-Za-z](?![A-Za-z0-9])")


def luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return total % 10 == 0


def _tokens(text: str) -> List[Tuple[int, int, str, bool]]:
    """(start, end, digits, ambiguous) for each digit token (numerals, digit words, "double"), None-digit tokens as ""."""
    out = []
    low = text.translate(_DEVA).lower()
    for m in _TOKEN.finditer(low):
        tok = m.group(0)
        if tok.isdigit() and tok.isascii():
            out.append((m.start(), m.end(), tok, False))
        elif tok in sd.MULTIPLIERS:
            out.append((m.start(), m.end(), "*" + str(sd.MULTIPLIERS[tok]), False))
        elif tok in sd.DIGIT_WORDS:
            out.append((m.start(), m.end(), str(sd.DIGIT_WORDS[tok]), tok in sd.AMBIGUOUS))
        else:
            out.append((m.start(), m.end(), "", False))
    return out


def _runs(text: str) -> List[Tuple[int, int, str, bool]]:
    """(start, end, digits, all_ambiguous) of each run of digit tokens in `text`."""
    toks = _tokens(text)
    runs: List[Tuple[int, int, str, bool]] = []
    cur: List[Tuple[int, int, str, bool]] = []
    mult = 1

    def flush() -> None:
        nonlocal cur
        if cur and not all(t[3] for t in cur):
            runs.append((cur[0][0], cur[-1][1], "".join(t[2] for t in cur), False))
        cur = []

    pending = None
    for tok in toks:
        s, e, d, amb = tok
        if cur or pending:
            prev_end = cur[-1][1] if cur else pending[1]
            gap = text[prev_end:s]
            if not _GAP.match(gap):
                flush()
                pending = None
        if d.startswith("*"):
            pending = (s, e, int(d[1:]))
            continue
        if not d:
            flush()
            pending = None
            continue
        if pending is not None:
            start = pending[0]
            digits = d * pending[2] if len(d) == 1 else d
            cur.append((start, e, digits, amb))
            pending = None
        else:
            cur.append((s, e, d, amb))
    flush()
    return runs


def _is_phone_with_country_code(digits: str) -> bool:
    return len(digits) == 12 and digits.startswith("91") and digits[2] in "6789"


def _spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    for s, e, digits, _ in _runs(text):
        n = len(digits)
        if n == 12 and not _is_phone_with_country_code(digits):
            spans.append((s, e))
        elif 13 <= n <= 19 and not (n == 13 and digits.startswith("091") and digits[3] in "6789") and luhn(digits):
            spans.append((s, e))
    # the digits after the word OTP
    for m in _OTP_WORD.finditer(text):
        pos = m.end()
        while True:
            w = re.match(r"[\s:=\-,]*([^\s:=\-,]+)", text[pos:])
            if w and w.group(1).lower() in _OTP_FILLER:
                pos += w.end()
            else:
                break
        for s, e, digits, _ in _runs(text[pos:pos + 80]):
            if s <= 3 and 4 <= len(digits) <= 8:
                spans.append((pos + s, pos + e))
            break
    for rx in (_PAN_COMPACT, _PAN_UPPER_SPACED, _PAN_LETTERS):
        spans.extend(m.span() for m in rx.finditer(text))
    return spans


def redact_ids(text: str) -> str:
    """`text` with identity numbers replaced by "[ID number removed]"; text without any is returned unchanged."""
    if not text or not isinstance(text, str):
        return text
    spans = sorted(_spans(text))
    if not spans:
        return text
    out, last = [], 0
    for s, e in spans:
        if s < last:
            last = max(last, e)       # overlapping span: already covered
            continue
        out.append(text[last:s])
        out.append(MARKER)
        last = e
    out.append(text[last:])
    return "".join(out)


def has_ids(text: str) -> bool:
    return bool(text) and redact_ids(text) != text
