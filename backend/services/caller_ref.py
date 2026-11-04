"""An opaque reference to a caller for the desk's screens: who a booking is for, without the phone number.

    caller_ref(owner, number) = first 16 hex characters of SHA-256 of  "<owner id>|<E.164 number>"

The E.164 number of a 10-digit Indian mobile is "+91" and the ten digits; a number with 12 digits starting 91 is the same
with a "+"; any other digit string is "+" and its digits. This is the key string `services/caller_profiles.py` stores a
caller's profile under ("<owner>|<number>"), so `caller_ref(owner, number)` can be matched against a profile with
`caller_ref(profile owner, profile number)`. The desk links a booking to `/desk/callers?caller=<caller_ref>`.
The reference is not reversible, but it is a stable name for a number: it goes only to the owner's own screens, never to
a public page."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

_NONDIGIT = re.compile(r"\D")


def e164(number: Any) -> Optional[str]:
    text = str(number or "").strip()
    digits = _NONDIGIT.sub("", text)
    if not digits:
        return None
    if len(digits) == 10:
        return "+91" + digits
    return "+" + digits


def caller_ref(owner: Any, number: Any) -> Optional[str]:
    n = e164(number)
    if not n:
        return None
    return hashlib.sha256(f"{owner}|{n}".encode("utf-8")).hexdigest()[:16]


def same_number(a: Any, b: Any) -> bool:
    return bool(e164(a)) and e164(a) == e164(b)
