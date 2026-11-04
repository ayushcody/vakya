"""Ears that learn: what a front desk's owner has taught Vakya about how its callers sound.

A business owner can see what Prisma heard ("Dr. Call Karni") and say what was meant ("Dr. Kulkarni"). Each correction is kept
per front desk and sent to Gnani's Prisma on the next call, in two ways described on
https://docs.gnani.ai/api/STT/speech-to-text.md:

* a **substitution rule** `{"patterns": ["Dr. Call Karni"], "replacement": "Dr. Kulkarni"}` in `substitution_map`, with
  `enable_substitution` true. Prisma applies it after recognition (case-insensitive, the longest phrase wins, one pass) and
  allows at most **10 rules**, so a desk has at most 10 corrections;
* the right words as **keyterms** (the desk's own vocabulary), which help Prisma hear them in the first place.

This module is the store, the owner-facing rules, the hints a turn sends (`prepare_turn_hints`, called by
`turn_pipeline.gnani_turn_hints`) and the live events ("Listening for: a name", "Rule applied: ..."). What the caller is about to
say is decided by services/hearing_expect.py.

The store is one JSON file (default backend/data/hearing.json, override with VAKYA_HEARING_FILE), written atomically under a
lock shared by threads and processes, like services/schedules.py:

    {"desks": {agent_id: {"rules": [Rule], "vocabulary": [Term], "next_rule": 1}}}
    Rule {id "H1", heard, meant, learnt_from {"conversation_id", "turn"} | "owner", created_at, last_used_at, uses}
    Term {term, added_by "correction" | "owner", created_at}

Nothing here is a number, an identity document or a phone number: text that looks like one is refused (services/id_redact.py)."""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
import tempfile
import threading
import unicodedata
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:
    from services import hearing_expect as expect, id_redact
except ImportError:  # pragma: no cover - imported as backend.services.hearing
    from backend.services import hearing_expect as expect, id_redact

log = logging.getLogger("vakya.hearing")

MAX_RULES = 10                  # Prisma's substitution_map limit
MAX_VOCABULARY = 40             # desk vocabulary; keyterms are limited to 200 in all, with the desk's names and terms
MAX_HEARD, MAX_MEANT = 60, 80   # characters
MIN_CHARS = 2
MAX_WORDS = 8
MAX_AGENTS = 500
MAX_KEYTERMS = 200

_thread_lock = threading.RLock()


class HearingError(ValueError):
    """Input that cannot be a correction: `code` is stable, `field` names the offending field, `status` the HTTP status."""

    def __init__(self, code: str, message: str, field: Optional[str] = None, details: Any = None, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.field, self.details, self.status = code, message, field, details, status


# ------------------------------------------------------------------------------------------------------- the file

def data_path() -> str:
    return os.getenv("VAKYA_HEARING_FILE", "").strip() or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "hearing.json")


def _read(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("desks"), dict):
            return data
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        print(f"hearing: {path} is unreadable ({e}); starting a new file", file=sys.stderr)
        with contextlib.suppress(OSError):
            os.replace(path, path + ".corrupt")
    return {"desks": {}}


def _write(path: str, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


@contextlib.contextmanager
def _transaction():
    """Load the data under a lock shared by threads and processes; it is saved when the block ends without error."""
    path = data_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _thread_lock:
        lock_file = open(path + ".lock", "a")
        try:
            if fcntl:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
            data = _read(path)
            before = json.dumps(data, sort_keys=True)
            yield data
            if json.dumps(data, sort_keys=True) != before:
                _write(path, data)
        finally:
            lock_file.close()


def _load() -> dict:
    return _read(data_path())


def _stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def _desk(data: dict, agent_id: str, create: bool = False) -> Optional[dict]:
    desk = data["desks"].get(agent_id)
    if desk is None and create:
        if len(data["desks"]) >= MAX_AGENTS:
            raise HearingError("TOO_MANY_DESKS", "Too many front desks have corrections.", status=409)
        desk = data["desks"][agent_id] = {"rules": [], "vocabulary": [], "next_rule": 1}
    return desk


# ------------------------------------------------------------------------------------------------------ validation

_DIGIT_RUN = re.compile(r"\d(?:[\s\-.]?\d){5,}")


def clean(text: Any) -> str:
    """The text as one tidy line: NFC, collapsed spaces, no control characters, no wrapping quotes."""
    s = unicodedata.normalize("NFC", str(text or ""))
    s = "".join(" " if unicodedata.category(c).startswith("C") else c for c in s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip("\"'`“”‘’").strip()


def _check_text(value: Any, field: str, limit: int) -> str:
    s = clean(value)
    if not s:
        raise HearingError("EMPTY", f"Say what was {'heard' if field == 'heard' else 'meant'}.", field)
    if len(s) < MIN_CHARS:
        raise HearingError("TOO_SHORT", f"'{s}' is too short to teach (at least {MIN_CHARS} characters).", field)
    if len(s) > limit:
        raise HearingError("TOO_LONG", f"That is too long (at most {limit} characters).", field)
    if len(s.split()) > MAX_WORDS:
        raise HearingError("TOO_LONG", f"That is too long (at most {MAX_WORDS} words).", field)
    if not any(c.isalnum() for c in s):
        raise HearingError("INVALID", "Use words, not only symbols.", field)
    if id_redact.has_ids(s) or _DIGIT_RUN.search(s):
        raise HearingError("ID_NUMBER", "Vakya never keeps a number that looks like a phone, card or identity number. "
                           "Leave the digits out.", field)
    return s


def check_pair(heard: Any, meant: Any) -> tuple:
    return _check_text(heard, "heard", MAX_HEARD), _check_text(meant, "meant", MAX_MEANT)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


# --------------------------------------------------------------------------------------------------------- reading

def _rule_out(r: dict) -> dict:
    return {"id": r["id"], "heard": r["heard"], "meant": r["meant"], "learnt_from": r.get("learnt_from") or "owner",
            "created_at": r.get("created_at"), "last_used_at": r.get("last_used_at"), "uses": int(r.get("uses") or 0)}


def list_rules(agent_id: str) -> List[dict]:
    desk = _desk(_load(), agent_id)
    return [_rule_out(r) for r in (desk or {}).get("rules", [])]


def list_vocabulary(agent_id: str) -> List[dict]:
    desk = _desk(_load(), agent_id)
    return [dict(t) for t in (desk or {}).get("vocabulary", [])]


def summary(agent_id: str) -> dict:
    """What the desk's settings page shows: the rules, the vocabulary and the limits."""
    rules, vocab = list_rules(agent_id), list_vocabulary(agent_id)
    return {"agent_id": agent_id, "rules": rules, "vocabulary": vocab, "rule_limit": MAX_RULES, "rules_used": len(rules),
            "vocabulary_limit": MAX_VOCABULARY,
            "note": "Gnani's Prisma accepts at most 10 substitution rules per request, so a desk keeps at most 10 corrections."}


def oldest_unused(rules: List[dict]) -> Optional[dict]:
    """The rule that has gone longest without being used (a rule never used counts from when it was made)."""
    def order(r: dict) -> tuple:
        n = re.sub(r"\D", "", str(r.get("id")))
        return (r.get("last_used_at") or r.get("created_at") or "", int(n) if n else 0)
    return min(rules, key=order, default=None)


# ------------------------------------------------------------------------------------------------------- changing

def _learnt_from(conversation_id: Optional[str], turn: Optional[int]) -> Any:
    cid = str(conversation_id or "").strip()
    if not cid:
        return "owner"
    out: Dict[str, Any] = {"conversation_id": cid[:80]}
    if turn is not None:
        out["turn"] = int(turn)
    return out


def add_correction(agent_id: str, heard: Any, meant: Any, *, conversation_id: Optional[str] = None,
                   turn: Optional[int] = None, replace_rule_id: Optional[str] = None, evict_oldest_unused: bool = False,
                   add_keyterm: bool = True) -> dict:
    """Teach the desk one correction: Prisma's `heard` becomes `meant`, and `meant` joins the desk's vocabulary (keyterms).
    Raises HearingError: EMPTY, TOO_SHORT, TOO_LONG, INVALID, ID_NUMBER (422), DUPLICATE, RULES_FULL (409). At 10 rules it
    fails with RULES_FULL (and says which rule is the oldest unused) unless `replace_rule_id` names a rule to replace or
    `evict_oldest_unused` is true. Returns {"rule", "replaced", "vocabulary_added", "vocabulary_full", "rules_used"}."""
    heard, meant = check_pair(heard, meant)
    if not agent_id:
        raise HearingError("INVALID", "Which front desk?", "agent_id")
    with _transaction() as data:
        desk = _desk(data, agent_id, create=True)
        rules = desk["rules"]
        same = next((r for r in rules if _fold(r["heard"]) == _fold(heard)), None)
        if same is not None:
            raise HearingError("DUPLICATE", f"Vakya already listens for '{same['heard']}' as '{same['meant']}'. Delete that "
                               "correction first to change it.", "heard", {"rule_id": same["id"]}, 409)
        replaced = None
        if len(rules) >= MAX_RULES:
            victim = None
            if replace_rule_id:
                victim = next((r for r in rules if r["id"] == replace_rule_id), None)
                if victim is None:
                    raise HearingError("NOT_FOUND", "That correction is not on this desk.", "replace_rule_id", status=404)
            elif evict_oldest_unused:
                victim = oldest_unused(rules)
            if victim is None:
                old = oldest_unused(rules)
                raise HearingError("RULES_FULL", f"This desk already has {MAX_RULES} corrections, the most Gnani's Prisma "
                                   "accepts. Delete one, or replace the oldest unused one.", None,
                                   {"limit": MAX_RULES, "oldest_unused": _rule_out(old) if old else None}, 409)
            rules.remove(victim)
            replaced = _rule_out(victim)
        rule = {"id": f"H{int(desk.get('next_rule') or 1)}", "heard": heard, "meant": meant,
                "learnt_from": _learnt_from(conversation_id, turn), "created_at": _stamp(), "last_used_at": None, "uses": 0}
        desk["next_rule"] = int(desk.get("next_rule") or 1) + 1
        rules.append(rule)
        added, full = False, False
        if add_keyterm:
            added, full = _add_term(desk, meant, "correction")
        return {"rule": _rule_out(rule), "replaced": replaced, "vocabulary_added": added, "vocabulary_full": full,
                "rules_used": len(rules)}


def delete_rule(agent_id: str, rule_id: str) -> dict:
    """Forget one correction. Its words stay in the vocabulary until the owner removes them there."""
    with _transaction() as data:
        desk = _desk(data, agent_id)
        rule = next((r for r in (desk or {}).get("rules", []) if r["id"] == rule_id), None)
        if rule is None:
            raise HearingError("NOT_FOUND", "That correction is not on this desk.", "rule_id", status=404)
        desk["rules"].remove(rule)
        return {"deleted": _rule_out(rule), "rules_used": len(desk["rules"])}


def _add_term(desk: dict, term: str, added_by: str) -> tuple:
    """(added, full): a term already there (any case) is not added twice; at the limit it is not added."""
    vocab = desk["vocabulary"]
    if any(_fold(t["term"]) == _fold(term) for t in vocab):
        return False, False
    if len(vocab) >= MAX_VOCABULARY:
        return False, True
    vocab.append({"term": term, "added_by": added_by, "created_at": _stamp()})
    return True, False


def add_vocabulary(agent_id: str, term: Any) -> dict:
    """The owner adds one word or short phrase the desk's callers say (a doctor, a dish, a place)."""
    s = _check_text(term, "term", MAX_MEANT)
    with _transaction() as data:
        desk = _desk(data, agent_id, create=True)
        added, full = _add_term(desk, s, "owner")
        if full:
            raise HearingError("VOCABULARY_FULL", f"This desk's vocabulary is full ({MAX_VOCABULARY} words). Remove one first.",
                               "term", {"limit": MAX_VOCABULARY}, 409)
        if not added:
            raise HearingError("DUPLICATE", f"'{s}' is already in this desk's vocabulary.", "term", status=409)
        return {"term": desk["vocabulary"][-1], "vocabulary_used": len(desk["vocabulary"])}


def delete_vocabulary(agent_id: str, term: str) -> dict:
    with _transaction() as data:
        desk = _desk(data, agent_id)
        row = next((t for t in (desk or {}).get("vocabulary", []) if _fold(t["term"]) == _fold(clean(term))), None)
        if row is None:
            raise HearingError("NOT_FOUND", "That word is not in this desk's vocabulary.", "term", status=404)
        desk["vocabulary"].remove(row)
        return {"deleted": row, "vocabulary_used": len(desk["vocabulary"])}


def mark_used(agent_id: str, rule_ids: List[str]) -> None:
    """Note that rules were probably applied on a call (the "oldest unused" eviction reads it). Never raises."""
    if not rule_ids:
        return
    try:
        with _transaction() as data:
            desk = _desk(data, agent_id)
            for r in (desk or {}).get("rules", []):
                if r["id"] in rule_ids:
                    r["last_used_at"] = _stamp()
                    r["uses"] = int(r.get("uses") or 0) + 1
    except Exception as e:  # noqa: BLE001 - a bookkeeping failure must never reach a call
        log.debug("could not note the use of a correction: %r", e)


# ----------------------------------------------------------------------------------------- what a turn sends Prisma

def substitution_map(rules: List[dict]) -> List[dict]:
    """The rules as Prisma's `substitution_map` wants them (docs: `[{"patterns": [...], "replacement": "..."}]`, at most 10)."""
    return [{"patterns": [r["heard"]], "replacement": r["meant"]} for r in rules[:MAX_RULES]]


DESK_WORDS = {"clinic": "clinic", "hotel": "hotel", "salon": "salon", "restaurant": "restaurant", "office": "office",
              "institute": "institute", "service_centre": "service centre", "fitness": "gym"}


def desk_description(agent: dict) -> str:
    """"the front desk of a clinic called Sunrise Clinic" (the trade from the template, the desk's own name)."""
    trade = DESK_WORDS.get(str((agent or {}).get("template_id") or ""), "")
    name = clean((agent or {}).get("name"))[:80]
    kind = f"a {trade}" if trade else "a small business"
    return f"the front desk of {kind}" + (f" called {name}" if name else "")


def known_names(conversation_id: Optional[str]) -> List[str]:
    """Names this conversation has already heard (services/caller_profiles.known_names), [] while that function does not exist
    or knows nothing. Never raises."""
    if not conversation_id:
        return []
    try:
        try:
            from services import caller_profiles  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            from backend.services import caller_profiles  # noqa: PLC0415
        names = caller_profiles.known_names(conversation_id)
        return [clean(n) for n in (names or []) if clean(n)][:4]
    except (ImportError, AttributeError):
        return []
    except Exception as e:  # noqa: BLE001 - a hint, never a reason to fail a turn
        log.debug("no known names for this turn: %r", e)
        return []


def last_agent_text(run: Any) -> Optional[str]:
    """What the desk said last: the phone call's own record of it, else the last agent row of the conversation history."""
    text = getattr(run, "asr_previous_agent_text", None)
    if text:
        return str(text)
    for h in reversed(getattr(run, "history", None) or []):
        if h.get("role") in ("agent", "assistant") and h.get("text_content"):
            return str(h["text_content"])
    return None


def turn_label(run: Any) -> Optional[str]:
    """The turn's number within its conversation (caller turns heard so far plus this one), else a short piece of the turn id."""
    explicit = getattr(run, "asr_turn_number", None)
    if explicit:
        return str(explicit)
    history = getattr(run, "history", None) or []
    heard = sum(1 for h in history if h.get("role") == "user")
    if history:
        return str(heard + 1)
    tid = str(getattr(run, "turn_id", "") or "")
    return tid[:8] or None


def prepare_turn_hints(run: Any, hints: dict, entity_terms: List[str], env_terms: int = 0) -> dict:
    """Fill the Prisma hints of one turn with what the desk has learnt and what the caller is about to say.

    Sets `topic`, `speaker_names`, `substitution_map` (the desk's rules), `request_id` (conversation id and turn, sent as
    X-API-Request-ID), `keyterms` (desk vocabulary first, then the words the expectation brings, the caller's known names,
    then the entities the desk's documents mention; 200 in all with the environment's), `expecting` (the primary kind: "name",
    "when", "digits", "yes_no" or "open"), `expecting_kinds`, and the private `_hearing` (rules and names, for the events).
    Emits the "Listening for" event when `run` can emit. Returns `hints`."""
    agent = getattr(run, "agent", None) or {}
    agent_id = agent.get("id") or getattr(run, "agent_id", None)
    conversation_id = getattr(run, "conversation_id", None) or getattr(run, "asr_conversation_id", None)
    guess = expect.detect(last_agent_text(run))
    kinds = guess["kinds"]
    names = known_names(conversation_id)
    desk = _desk(_load(), agent_id) if agent_id else None
    rules = [_rule_out(r) for r in (desk or {}).get("rules", [])]
    vocab = [t["term"] for t in (desk or {}).get("vocabulary", [])]

    seen, terms = set(), []
    for t in [*vocab, *expect.terms_for(kinds), *names, *entity_terms]:
        t = clean(t)
        if t and _fold(t) not in seen:
            seen.add(_fold(t))
            terms.append(t)
    hints["keyterms"] = terms[:max(0, MAX_KEYTERMS - env_terms)]
    hints["expecting"] = guess["primary"]
    hints["expecting_kinds"] = kinds
    hints["topic"] = expect.topic_for(kinds, desk_description(agent), names, [*vocab[:6], *entity_terms[:8]])
    hints["speaker_names"] = names[:4]
    hints["substitution_map"] = substitution_map(rules)
    try:
        from services import gnani_client  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from backend.services import gnani_client  # noqa: PLC0415
    hints["request_id"] = gnani_client.request_id_for(conversation_id, turn_label(run))
    emit = getattr(run, "emit", None)
    hints["_hearing"] = {"agent_id": agent_id, "rules": rules, "names": names[:3],
                         "emit": emit if callable(emit) else None, "listening_sent": False}
    emit_listening(hints)
    return hints


# ------------------------------------------------------------------------------------------------------------ events

def listening_label(kinds: List[str], names: Optional[List[str]] = None) -> str:
    """"Listening for: a name (Sunita, Rahul)", "Listening for: a day or time", "Listening for: yes or no"."""
    text = "Listening for: " + expect.label_for(kinds)
    if "name" in kinds and names:
        text += " (" + ", ".join(names[:3]) + ")"
    return text


def source_phrase(rule: dict) -> str:
    """"rule learnt from call #12" or "rule you added"."""
    src = rule.get("learnt_from")
    if isinstance(src, dict) and src.get("conversation_id"):
        ref = str(src["conversation_id"])
        return f"rule learnt from call #{ref if ref.isdigit() else ref[:6]}"
    return "rule you added"


def corrected_label(rule: dict) -> str:
    """Said as a guess: Prisma's own text before the rule is not visible, so this is "probably"."""
    return f"Probably heard '{rule['heard']}', corrected to '{rule['meant']}' ({source_phrase(rule)})"


def probably_applied(rules: List[dict], transcript: str) -> List[dict]:
    """The rules that probably ran on this transcript: `meant` is in it and `heard` is not. Prisma applies the rules itself
    and the text before them is not returned, so this is a guess from the final text (and a caller who simply said `meant`
    is counted too)."""
    text = _fold(transcript or "")
    out = []
    for r in rules:
        if _fold(r["meant"]) in text and _fold(r["heard"]) not in text:
            out.append(r)
    return out


def emit_listening(hints: dict) -> None:
    """The "Listening for" event of this turn, once (nothing for open speech). Never raises."""
    h = hints.get("_hearing") or {}
    kinds = hints.get("expecting_kinds") or []
    if h.get("listening_sent") or not kinds or not callable(h.get("emit")):
        return
    try:
        h["listening_sent"] = True
        h["emit"]("hearing", kind="listening", expecting=hints.get("expecting"), kinds=kinds,
                  names=h.get("names") or [], label=listening_label(kinds, h.get("names")))
    except Exception as e:  # noqa: BLE001
        log.debug("listening event skipped: %r", e)


def after_transcript(hints: dict, transcript: str) -> List[dict]:
    """Once Prisma has answered: note the rules that probably ran, emit one event for each, and keep them in
    `hints["rules_applied"]`. Never raises. Returns the rules."""
    try:
        h = hints.get("_hearing") or {}
        applied = probably_applied(h.get("rules") or [], transcript)
        hints["rules_applied"] = [r["id"] for r in applied]
        if applied and h.get("agent_id"):
            mark_used(h["agent_id"], [r["id"] for r in applied])
        emit: Optional[Callable] = h.get("emit")
        if callable(emit):
            for r in applied:
                emit("hearing", kind="corrected", probable=True, rule={"id": r["id"], "heard": r["heard"], "meant": r["meant"],
                                                                        "learnt_from": r.get("learnt_from")},
                     label=corrected_label(r))
        return applied
    except Exception as e:  # noqa: BLE001
        log.debug("rule events skipped: %r", e)
        return []
