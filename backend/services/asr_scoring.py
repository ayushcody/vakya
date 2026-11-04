"""Scoring speech recognition on Hinglish honestly: the same words in two scripts must not count as errors.

The 15-clip Hinglish bench of 20 Oct 2026 scored WER 0.799, with plain `jiwer.wer` against Roman references while the
recogniser often answered in Devanagari: a perfect Devanagari transcript of a Roman reference scores about 1.0. Here both
sides are brought to one Roman spelling before scoring:

  1. Devanagari is transliterated to Roman (indic_transliteration, MIT; the ITRANS scheme).
  2. Roman spellings are folded: lower case, diacritics removed, long vowels collapsed (aa->a, ee->i, oo->u, ai->e,
     au->o), aspirates dropped (kh->k, bh->b, sh->s), w->v, z->j, c->k except in "ch", every non-initial "a" dropped
     (the schwa: Roman Hinglish writes "kitna", "naam", "subah"; ITRANS writes "kitanA", "nAma", "subaha"), doubled
     letters collapsed, punctuation removed. English loanwords keep a residue ("doctor" against "DOkTara" folds to
     "doktor" against "doktr"), which the CER reports as a fraction of a word rather than a whole wrong word.

Folding loses distinctions (it is for scoring, never for display), so a WER on folded text is a lower bound on the
real error and a CER on it is the more forgiving, spelling-tolerant view. Report raw WER, folded WER and folded CER
side by side; the folded numbers are the ones to compare recognisers by for Hinglish.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Optional

DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def has_devanagari(text: str) -> bool:
    return bool(DEVANAGARI_RE.search(text or ""))


def transliterate(text: str) -> str:
    """Devanagari runs of `text` in Roman letters (ITRANS); other characters unchanged. Without the library, the
    Devanagari is left as it is (the fold then still scores it, just badly)."""
    if not has_devanagari(text):
        return text
    try:
        from indic_transliteration import sanscript
        from indic_transliteration.sanscript import transliterate as _tr
    except ImportError:  # pragma: no cover - the bench venv has it
        return text
    # ITRANS has no sign for the candra vowels of English loanwords (डॉक्टर, बैंक): read them as plain o / e first.
    text = text.replace("ॉ", "ो").replace("ऑ", "ओ").replace("ॅ", "े")
    out = []
    for run in re.split(r"([ऀ-ॿ]+)", text):
        if has_devanagari(run):
            run = _tr(run, sanscript.DEVANAGARI, sanscript.ITRANS).replace("M", "n")   # anusvara -> n
        out.append(run)
    return "".join(out)


_FOLD_STEPS = (
    (re.compile(r"[.~^'`\"]"), ""),              # ITRANS marks (anusvara ".n", visarga ".h", chandra "~")
    (re.compile(r"[^a-z0-9 ]+"), " "),            # punctuation (Devanagari danda included) to spaces
    (re.compile(r"aa"), "a"),                     # long vowels, before the schwa rule below
    (re.compile(r"ee|ii"), "i"),
    (re.compile(r"oo|uu"), "u"),
    (re.compile(r"ai"), "e"),
    (re.compile(r"au"), "o"),
    (re.compile(r"c(?!h)"), "k"),                 # c -> k, but "ch" stays a sound of its own
    (re.compile(r"(?<=[kgjtdpbs])h"), ""),       # aspirates: kh gh th dh ph bh -> k g t d p b; sh -> s
    (re.compile(r"ch"), "c"),
    (re.compile(r"w"), "v"),
    (re.compile(r"z"), "j"),
    (re.compile(r"q"), "k"),
    (re.compile(r"x"), "ks"),
    (re.compile(r"aya?\b"), "e"),                 # Roman "chai" (-> che) against ITRANS "chAya" (-> chaya -> che)
    (re.compile(r"(?<=[a-z])a"), ""),             # schwa: Roman Hinglish writes "kitna", ITRANS "kitanA"; both -> kitn
    (re.compile(r"iy"), "i"),                     # "chahiye" against "chAhie"
    (re.compile(r"([a-z])\1+"), r"\1"),           # doubled letters
    (re.compile(r"\s+"), " "),
)


def fold(text: str) -> str:
    """One Roman spelling for scoring: transliterate, strip accents, lower-case, then the folds above."""
    s = transliterate(text or "").translate(_DEVANAGARI_DIGITS)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    for pattern, repl in _FOLD_STEPS:
        s = pattern.sub(repl, s)
    return s.strip()


def wer(ref: str, hyp: str) -> float:
    import jiwer
    r, h = ref.strip(), hyp.strip()
    if not r:
        return 0.0 if not h else 1.0
    return 1.0 if not h else float(jiwer.wer(r, h))


def cer(ref: str, hyp: str) -> float:
    import jiwer
    r, h = ref.strip(), hyp.strip()
    if not r:
        return 0.0 if not h else 1.0
    return 1.0 if not h else float(jiwer.cer(r, h))


def hinglish_scores(ref: str, hyp: str) -> Dict[str, Optional[float]]:
    """Raw WER (the old number), folded WER and folded CER for one Hinglish clip."""
    raw_ref = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", unicodedata.normalize("NFKC", ref).lower().replace("’", "'")).replace("'", "")).strip()
    raw_hyp = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", unicodedata.normalize("NFKC", hyp).lower().replace("’", "'")).replace("'", "")).strip()
    fr, fh = fold(ref), fold(hyp)
    return {"wer_raw": wer(raw_ref, raw_hyp), "wer_folded": wer(fr, fh), "cer_folded": cer(fr, fh),
            "hyp_devanagari": has_devanagari(hyp)}


# ---------------------------------------------------------------------------------- entities and accepted spellings
#
# A booking is not lost by a wrong filler word; it is lost when the name, the day or the time is wrong. The stress report
# (bench/stress_report.py) therefore scores the entities a caller gives, not only the words: each entity carries a list of
# accepted spellings (Devanagari and Roman, "प्रिया", "priya", "priyaa"; the idea of Voice of India's accepted-spelling
# scoring) and counts as hit when any one of them appears in the folded transcript (entity hit rate, as in the TTS-STT
# Flywheel paper, arXiv 2605.03073). A spelling is matched as a run of whole folded words, so a short name does not hit
# inside a longer word. Both sides go through `fold`, so "mangalvar", "मंगलवार" and "मंगलवारी" mostly meet in one form;
# the list still carries the variants a human would accept, for the ones the fold does not join.

BOOKING_ENTITIES = ("name", "date", "time")   # a booking can be completed when all three were heard


def _as_spellings(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value if str(v).strip()]


def _contains_run(words: list, run: list) -> bool:
    n = len(run)
    return n > 0 and any(words[i:i + n] == run for i in range(len(words) - n + 1))


def _digit_runs(words: list) -> list:
    """Runs of adjacent all-digit words joined into one string: "9876 543 210" -> "9876543210"."""
    runs, cur = [], ""
    for w in words:
        if w.isdigit():
            cur += w
        else:
            if cur:
                runs.append(cur)
            cur = ""
    if cur:
        runs.append(cur)
    return runs


def _digit_match(spelling: str, runs: list) -> bool:
    """A digits-only spelling of 4 or more digits (an amount, a phone number) matches however the recogniser grouped
    them: "9876-543-210" equals "9876543210"; a 10-digit one also ends a longer run (a "+91" or "0" prefix, up to 3 digits;
    an extra digit after it is an error)."""
    digits = "".join(fold(spelling).split())
    if not digits.isdigit() or len(digits) < 4:
        return False
    return any(r == digits or (len(digits) >= 10 and r.endswith(digits) and len(r) - len(digits) <= 3) for r in runs)


def entity_hits(hyp: str, entities: Dict[str, object]) -> Dict[str, bool]:
    """For each entity kind, whether any accepted spelling appears (as whole folded words) in `hyp`; digit spellings of
    4 or more digits also match when the recogniser grouped the digits differently."""
    words = fold(hyp).split()
    runs = _digit_runs(words)
    hits: Dict[str, bool] = {}
    for kind, spellings in (entities or {}).items():
        hits[kind] = any(_contains_run(words, fold(s).split()) or _digit_match(s, runs) for s in _as_spellings(spellings))
    return hits


def entity_hit_rate(hyp: str, entities: Dict[str, object]) -> float:
    """Fraction of the entities (name, date, time, amount, phone, as given) that appear in `hyp` under any accepted
    spelling; 1.0 when there are no entities to find."""
    hits = entity_hits(hyp, entities)
    return sum(hits.values()) / len(hits) if hits else 1.0


def accepted_wer(ref_variants, hyp: str, folded: bool = True) -> float:
    """The lowest WER of `hyp` over the accepted reference spellings (a string or a list of strings). With `folded`
    (default) both sides are folded first, the spelling-tolerant WER; without it the plain `wer`."""
    refs = _as_spellings(ref_variants)
    if not refs:
        return 0.0 if not (hyp or "").strip() else 1.0
    prep = fold if folded else (lambda s: s)
    h = prep(hyp or "")
    return min(wer(prep(r), h) for r in refs)


def task_ok(hyp: str, entities: Dict[str, object], required=BOOKING_ENTITIES) -> bool:
    """The booking could be completed from this transcript: the name AND the date AND the time were all heard. Amount
    and phone are scored by the entity hit rate but do not gate the task (a receptionist can ask for them again)."""
    gate = {k: v for k, v in (entities or {}).items() if k in required}
    return bool(gate) and all(entity_hits(hyp, gate).values())
