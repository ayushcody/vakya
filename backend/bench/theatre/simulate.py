"""Caller Theatre without audio and without the API: the scripted caller speaks TEXT, the scripted model answers, and the REAL tool
loop (services/tools.py), schedules, availability rules, read-back and confirm-before-cancel run against a private store in a temp
folder. It shows that a scenario's script and the scripted model agree end to end and that the scorer can pass it, in about a second.

It proves nothing about speech recognition or voices (the caller's words reach the agent exactly as scripted) and nothing about a real
language model. Results from here are labelled "simulated" and never written to results/ or shown on the page."""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
for p in (str(BACKEND), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from . import scenarios as sc, scorer, scripted_llm, speech
except ImportError:
    import scenarios as sc, scorer, scripted_llm, speech  # type: ignore

AGENT = "theatre-sim"


@contextmanager
def private_stores(workdir: Path):
    """Point the schedule and appointment stores at `workdir` and the language model at the scripted policy; restore afterwards."""
    keep = {k: os.environ.get(k) for k in ("VAKYA_SCHEDULES_FILE", "VAKYA_APPOINTMENTS_FILE", "VAKYA_TOOL_PROTOCOL")}
    os.environ["VAKYA_SCHEDULES_FILE"] = str(workdir / "schedules.json")
    os.environ["VAKYA_APPOINTMENTS_FILE"] = str(workdir / "appointments.json")
    os.environ["VAKYA_TOOL_PROTOCOL"] = "json"
    from services import llm
    original_chat = llm.chat

    def chat(messages, **kw):
        text = scripted_llm.completion(messages, bool(kw.get("json_mode")))
        return llm.ChatResult(text, "openai", scripted_llm_model(), 1, {})

    llm.chat = chat
    try:
        yield
    finally:
        llm.chat = original_chat
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def scripted_llm_model() -> str:
    return "theatre-scripted"


def simulate(s: Dict[str, Any], vocab: Dict[str, Any], workdir: Path, today: Optional[date] = None) -> Dict[str, Any]:
    """Run one scenario in text. Returns the record the runner would write (without audio), with its verdict."""
    from services import schedule_bookings as sb, schedule_tools, schedules as sch, tools
    today = today or date.today()
    with private_stores(workdir):
        tpl = sc.json.loads((sc.TEMPLATES_DIR / f"{s['template']}.json").read_text(encoding="utf-8")) if hasattr(sc, "json") else None
        import json
        tpl = json.loads((sc.TEMPLATES_DIR / f"{s['template']}.json").read_text(encoding="utf-8"))
        spec = dict(tpl["schedules"][0])
        spec["agent_id"] = AGENT
        schedule = sch.create(spec)
        nights = s["target"].get("nights") or 1
        target, other = sc.pick_days(schedule, s["target"].get("time"), nights, today)
        ctx = sc.make_context(s, target, other)
        step = int(schedule.get("slot_minutes") or 30) + int(schedule.get("buffer_minutes") or 0)
        if s["target"].get("time"):
            h, m = speech.parse_hhmm(s["target"]["time"])
            ctx["expected_later"] = f"{(h * 60 + m + step) // 60:02d}:{(h * 60 + m + step) % 60:02d}"
        preset: List[str] = []
        for eb in s["setup"].get("existing_bookings", []):
            day = target if eb["date"] == "target" else other
            row = sb.create(schedule, day=day, at=eb.get("time"), name=eb["name"],
                            phone=ctx["phone"] if eb["phone"] == "caller" else eb["phone"], party=eb.get("party"))
            preset.append(row["booking_id"])
        before = [b["booking_id"] for b in sb._ap().all_bookings()]
        specs = schedule_tools.specs_for_agent(AGENT, tools)
        system = tpl["system_instructions"].replace("{org}", s["org"]).replace(" in {city}", f" in {s['city']}")
        brain = sc.CallerBrain(s, vocab, ctx)
        history: List[dict] = []
        rows: List[dict] = []
        turns: List[dict] = []
        reply: Optional[str] = None
        for i in range(1, int(s["max_turns"]) + 1):
            line = brain.next_line(reply)
            if line is None:
                break
            spoken = line.tts      # what the voice says, with digits and names as the recogniser will write them
            msgs = [{"role": "system", "content": system}] + history + [{"role": "user", "content": spoken}]
            known_text = " ".join(m["content"] for m in history)
            tt = tools.run_tool_turn(msgs, specs, previous_agent_text=reply, known_text=known_text,
                                     known_slots=tools.known_slots(rows))
            reply = tt.text
            history += [{"role": "user", "content": spoken}, {"role": "assistant", "content": reply}]
            records = [c.record() for c in tt.calls]
            rows.append({"tool_calls": records})
            turns.append({"i": i, "caller": {"move": line.move, "text": line.text, "repeat": line.repeat}, "heard": spoken,
                          "agent": {"text": reply, "tool_calls": records, "llm_model": "theatre-scripted"},
                          "latency": {"first_answer_ms": tt.llm_ms}, "wall_ms": tt.llm_ms + tt.tools_ms})
            stop = s["expect"].get("stop_after")
            if stop and any(c.ok and c.name.rsplit(".", 1)[-1] == ("cancel" if stop == "cancel" else "book") for c in tt.calls):
                break
        from routers.bookings import _zone, public_booking
        ap = sb._ap()
        zone = _zone(ap)
        bookings_after = [public_booking(b, zone) for b in ap.all_bookings()]
        rec = {"id": s["id"], "n": s["n"], "turns": turns, "dates": {"target": target.isoformat(), "other": other.isoformat()}}
        rec["verdict"] = scorer.score(s, ctx, rec, bookings_after, before, preset)
        # the scorer reads the phone from the book call; the simulated store keeps the full number: the same check applies
        return rec


def simulate_all(only: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    data = sc.load()
    out = []
    for s in data["scenarios"]:
        if only and s["n"] not in only:
            continue
        with tempfile.TemporaryDirectory() as td:
            out.append(simulate(s, data["vocab"], Path(td)))
    return out


if __name__ == "__main__":
    for r in simulate_all():
        v = r["verdict"]
        print(f"{r['n']}. {'PASS' if v['pass'] else 'FAIL'}  {v['reason']}")
        for t in r["turns"]:
            print(f"    caller: {t['caller']['text'][:80]}")
            print(f"    agent : {t['agent']['text'][:110]}   {[(c['name'], c['ok']) for c in t['agent']['tool_calls']]}")
