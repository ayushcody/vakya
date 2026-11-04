"""The Caller Theatre scenarios: loading and checking the data (scenarios.json), picking the days a run uses, and the caller
who branches on what the agent just said.

A scenario is data: who calls, in which language, over which line (8 kHz G.711, with or without noise), what is set up before
the call (bookings that already exist, a slot that is already taken), the caller's `moves` and what a pass is (`expect`).
A move is one thing the caller can say. It carries a `when` trigger on the agent's last reply (substrings, `@vocab` lists,
how many times it offered, whether it read a given number back); the first unused move whose trigger matches is spoken, so a
different reply gives a different next line. `first` marks the opening line. A move is used `max_uses` times (default 1)."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from . import speech
except ImportError:  # run as a script from this folder
    import speech  # type: ignore

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
SCENARIOS_PATH = HERE / "scenarios.json"
TEMPLATES_DIR = BACKEND / "templates" / "agents"
LANGUAGES = speech.LANGS
EXPECT_KEYS = {"bookings", "cancelled", "no_new_bookings", "readback_catch", "offered_alternatives", "recognised_by_name",
               "aadhaar", "stop_after"}


class ScenarioError(ValueError):
    pass


def load(path: Path = SCENARIOS_PATH) -> Dict[str, Any]:
    """The scenario file as {"vocab": {...}, "scenarios": [...]}, checked (ScenarioError lists every problem)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = check(data)
    if problems:
        raise ScenarioError("; ".join(problems))
    return data


def check(data: Dict[str, Any]) -> List[str]:
    """Every problem with the scenario data, as sentences (empty when it is sound)."""
    out: List[str] = []
    vocab = data.get("vocab") or {}
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        return ["no scenarios"]
    seen = set()
    for sc in scenarios:
        sid = sc.get("id", "?")
        if sid in seen:
            out.append(f"{sid}: duplicate id")
        seen.add(sid)
        for key in ("id", "n", "title", "persona", "template", "language", "channel", "caller", "moves", "expect", "target"):
            if key not in sc:
                out.append(f"{sid}: missing {key}")
        if sc.get("language") not in LANGUAGES:
            out.append(f"{sid}: language must be one of {', '.join(LANGUAGES)}")
        if sc.get("template") and not (TEMPLATES_DIR / f"{sc['template']}.json").exists():
            out.append(f"{sid}: unknown template {sc['template']}")
        ch = sc.get("channel") or {}
        if ch.get("codec") not in ("g711", "tel8k"):
            out.append(f"{sid}: channel.codec must be g711 or tel8k")
        snr = ch.get("snr_db")
        if snr is not None and not isinstance(snr, (int, float)):
            out.append(f"{sid}: channel.snr_db must be a number or null")
        moves = sc.get("moves") or []
        if not moves or not moves[0].get("first"):
            out.append(f"{sid}: the first move must have first: true")
        ids = [m.get("id") for m in moves]
        if len(set(ids)) != len(ids):
            out.append(f"{sid}: duplicate move ids")
        caller = sc.get("caller") or {}
        ctx_keys = set(caller) | {"day", "time", "later_time", "earlier_time", "date_long", "last4", "nights", "party"}
        for m in moves:
            say = m.get("say") or {}
            if not say.get("text"):
                out.append(f"{sid}/{m.get('id')}: a move needs say.text")
            for field_name in ("text", "tts"):
                for ph in speech.placeholders(say.get(field_name, "")):
                    base = ph.split("[")[0]
                    if base not in ctx_keys and base not in ("day", "time", "phone", "name", "nights", "party", "aadhaar",
                                                             "phone_slip", "last4", "later_time", "earlier_time"):
                        out.append(f"{sid}/{m.get('id')}: unknown placeholder {{{ph}}}")
                    elif base in ("phone", "phone_slip", "aadhaar", "name", "nights", "party") and base not in caller \
                            and base not in ("nights", "party"):
                        out.append(f"{sid}/{m.get('id')}: {{{base}}} is not in caller")
            when = m.get("when") or {}
            for lst in (when.get("agent_any") or []) + (when.get("agent_none") or []):
                if isinstance(lst, str) and lst.startswith("@") and lst[1:] not in vocab:
                    out.append(f"{sid}/{m.get('id')}: unknown vocab {lst}")
            if when.get("after") and when["after"] not in ids:
                out.append(f"{sid}/{m.get('id')}: after names no move")
        exp = sc.get("expect") or {}
        for k in exp:
            if k not in EXPECT_KEYS:
                out.append(f"{sid}: unknown expect key {k}")
        for b in exp.get("bookings") or []:
            for k in ("schedule", "date"):
                if k not in b:
                    out.append(f"{sid}: an expected booking needs {k}")
        for eb in (sc.get("setup") or {}).get("existing_bookings") or []:
            if eb.get("date") not in ("target", "other"):
                out.append(f"{sid}: an existing booking's date must be target or other")
        if sc.get("max_turns", 0) < 2:
            out.append(f"{sid}: max_turns must be at least 2")
    return out


def expand_terms(terms: Any, vocab: Dict[str, List[str]]) -> List[str]:
    out: List[str] = []
    for t in terms or []:
        if isinstance(t, str) and t.startswith("@"):
            out.extend(vocab.get(t[1:], []))
        else:
            out.append(str(t))
    return [t.lower() for t in out]


# ------------------------------------------------------------------------------------------------ choosing the days

def _step(schedule: dict) -> int:
    return int(schedule.get("slot_minutes") or 30) + int(schedule.get("buffer_minutes") or 0)


def _minutes(hhmm: str) -> int:
    h, m = speech.parse_hhmm(hhmm)
    return h * 60 + m


def is_open_for(schedule: dict, day: date, at: Optional[str], nights: int = 1) -> bool:
    """Would `day` (and `at`, a slot start) be bookable on this schedule's week? Slots: `at` is a slot start inside one of the
    day's ranges. Nights: every night of the stay is an open day. Closed dates are respected. Past and notice rules are the
    caller's: the days picked start tomorrow."""
    names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    closed = set(schedule.get("closed_dates") or [])
    days = [day + timedelta(days=i) for i in range(max(1, nights if schedule.get("kind") == "nights" else 1))]
    for d in days:
        if d.isoformat() in closed or not (schedule.get("week") or {}).get(names[d.weekday()]):
            return False
    if schedule.get("kind") == "nights" or not at:
        return True
    ranges = schedule["week"][names[day.weekday()]]
    step, want = _step(schedule), _minutes(at)
    for r in ranges:
        a, b = _minutes(r["start"]), _minutes(r["end"])
        if a <= want and want + int(schedule.get("slot_minutes") or 30) <= b and (want - a) % step == 0:
            return True
    return False


def pick_days(schedule: dict, at: Optional[str], nights: int, today: date) -> Tuple[date, date]:
    """(target, other): the first day from tomorrow on, within six days so a bare weekday name means it, that fits `at`; and a
    second fitting day (before the target when there is one, else after, up to twelve days out) for scenarios that need two."""
    fitting = [today + timedelta(days=i) for i in range(1, 13) if is_open_for(schedule, today + timedelta(days=i), at, nights)]
    near = [d for d in fitting if (d - today).days <= 6]
    if not near:
        raise ScenarioError(f"no day in the next six fits {at or 'a stay'} on {schedule.get('name')}")
    target = near[0]
    others = [d for d in fitting if d != target]
    other = ([d for d in others if d < target] or others or [target])[0]
    return target, other


# ----------------------------------------------------------------------------------------------------- the caller

@dataclass
class Line:
    move: str
    text: str            # what is shown
    tts: str             # what is spoken
    repeat: bool = False


@dataclass
class CallerBrain:
    """The caller. `next_line(agent_reply)` is None before the call (the opening line) and the agent's text afterwards."""
    scenario: Dict[str, Any]
    vocab: Dict[str, List[str]]
    ctx: Dict[str, Any]
    uses: Dict[str, int] = field(default_factory=dict)
    last: Optional[Line] = None
    clarify_repeats: int = 0
    history: List[Line] = field(default_factory=list)

    def _fmt(self, template: str, mode: str) -> str:
        return speech.fmt(template, self.ctx, self.scenario["language"], mode)

    def _line(self, move: Dict[str, Any]) -> Line:
        say = move["say"]
        return Line(move["id"], self._fmt(say["text"], "text"), self._fmt(say.get("tts") or say["text"], "tts"))

    def observe(self, reply: str) -> None:
        """Keep what the agent offered: its earlier and later times."""
        offered = sorted(speech.times_in(reply))
        if offered:
            self.ctx["earlier_time"], self.ctx["later_time"] = offered[0], offered[-1]

    def matches(self, when: Dict[str, Any], reply: str) -> bool:
        low = (reply or "").lower()
        any_terms = expand_terms(when.get("agent_any"), self.vocab)
        if any_terms and not any(t in low for t in any_terms):
            return False
        if any(t in low for t in expand_terms(when.get("agent_none"), self.vocab)):
            return False
        if when.get("agent_times_min") and len(speech.times_in(reply)) < int(when["agent_times_min"]):
            return False
        if when.get("agent_says_digits"):
            key = re.sub(r"[{}]", "", str(when["agent_says_digits"]))
            if not speech.says_digits(reply, str(self.ctx.get(key, ""))):
                return False
        if when.get("after") and not self.uses.get(when["after"]):
            return False
        return True

    def next_line(self, reply: Optional[str]) -> Optional[Line]:
        moves = self.scenario["moves"]
        if reply is None:
            line = self._line(moves[0])
            self.uses[moves[0]["id"]] = 1
            self.last = line
            self.history.append(line)
            return line
        self.observe(reply)
        low = reply.lower()
        if self.last is not None and self.clarify_repeats < 2 and \
                any(t in low for t in expand_terms(["@clarify"], self.vocab)) and not speech.times_in(reply):
            self.clarify_repeats += 1
            again = Line(self.last.move, self.last.text, self.last.tts, repeat=True)
            self.history.append(again)
            return again
        for m in moves[1:]:
            if self.uses.get(m["id"], 0) >= int(m.get("max_uses", 1)):
                continue
            if self.matches(m.get("when") or {}, reply):
                self.uses[m["id"]] = self.uses.get(m["id"], 0) + 1
                line = self._line(m)
                self.last = line
                self.history.append(line)
                return line
        return None


def make_context(scenario: Dict[str, Any], target: date, other: date) -> Dict[str, Any]:
    """Everything a caller line may name, for this run."""
    ctx: Dict[str, Any] = dict(scenario["caller"])
    ctx["date"] = target
    ctx["other_date"] = other
    tgt = scenario.get("target") or {}
    if tgt.get("time"):
        ctx["time"] = tgt["time"]
    if tgt.get("nights"):
        ctx["nights"] = tgt["nights"]
    return ctx
