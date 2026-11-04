#!/usr/bin/env python3
"""Caller Theatre: scripted synthetic callers run end to end against the running app and scored automatically.

    backend/.venv/bin/python backend/bench/theatre/runner.py [--only 1,5] [--scripted-llm] [--dry-run] [--keep]

For every scenario (scenarios.json) the runner
  1. creates a fresh front desk from the scenario's template through the API (POST /api/agents/from-template) and sets up the
     schedule state the scenario needs (a taken slot, a booking to cancel) through POST /api/bookings,
  2. lets a scripted caller speak. AUDIO RUN (switches on by itself when GNANI_API_KEY is set): each line is synthesised with Gnani
     Timbre, degraded to 8 kHz G.711 with the scenario's noise and sent as audio to the streaming turn endpoint, the path the web call
     uses. TEXT RUN (no key yet, labelled "text run (audio run pending)"): the same lines are sent as typed text to the turn
     endpoint; speech recognition, voices and the line noise are not exercised, scoring is identical. The next line depends on what the
     agent just said (scenarios.CallerBrain),
  3. records per turn what the agent heard, what it said, its tool calls (the `done` event's tool_calls, plus any `tool` events the
     stream carries), the latency and both audio files,
  4. reads the bookings back and scores the END STATE (scorer.py),
  5. deletes the front desk and cancels the bookings it made.
Results go to bench/theatre/results/<timestamp>-<mode>-<input>.json, latest.json and latest-<mode>.json, and the table in docs/THEATRE.md.

Model modes, labelled in every result: `scripted` (`--scripted-llm`: the app's model step is the deterministic stand-in of
scripted_llm.py; tools, bookings and rules are real) and `live` (`--allow-live`, a configured hosted model; refused unless asked for). Input modes: `audio` and `text`, above. `--dry-run` checks the scenarios and what the pipeline
would read from each line, and calls nothing."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import platform
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
BACKEND = BENCH.parent
REPO = BACKEND.parent
for p in (str(BACKEND), str(BENCH), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from . import audio, scenarios as sc, scorer, speech
except ImportError:  # run as a script
    import audio, scenarios as sc, scorer, speech  # type: ignore

RESULTS_DIR = HERE / "results"
CACHE_DIR = RESULTS_DIR / "cache"
DOC_PATH = REPO / "docs" / "THEATRE.md"
DOC_BEGIN, DOC_END = "<!-- theatre:results:begin -->", "<!-- theatre:results:end -->"
SCRIPTED_MODEL = "theatre-scripted"
API_LANG = {"en": "en", "hi": "hi", "mr": "mr", "hinglish": "hi"}


# ------------------------------------------------------------------------------------------------ small helpers

def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def log(msg: str) -> None:
    print(msg, flush=True)


def schedule_of(scenario: Dict[str, Any]) -> dict:
    """The first schedule of the scenario's template (the one the scenario books into)."""
    tpl = json.loads((sc.TEMPLATES_DIR / f"{scenario['template']}.json").read_text(encoding="utf-8"))
    return tpl["schedules"][0]


def fill_phone(value: Any, ctx: Dict[str, Any]) -> str:
    return ctx["phone"] if value == "caller" else str(value)


# ----------------------------------------------------------------------------------------------------- dry run

def dry_run(data: Dict[str, Any], only: Optional[List[int]] = None) -> int:
    """Validate the scenario data and show what the app's own readers (services/when.py for days and times, the caller-details
    rules for the phone) make of each line, spoken perfectly. Nothing is called. Returns the number of problems."""
    from datetime import date
    try:
        from services import caller_memory, when
    except ImportError:  # pragma: no cover
        from backend.services import caller_memory, when
    problems = 0
    log(f"{len(data['scenarios'])} scenarios, vocab: {', '.join(data['vocab'])}")
    today = date.today()
    for s in data["scenarios"]:
        if only and s["n"] not in only:
            continue
        sched = schedule_of(s)
        nights = (s["target"].get("nights") or 1)
        try:
            target, other = sc.pick_days(sched, s["target"].get("time"), nights, today)
        except sc.ScenarioError as e:
            log(f"  {s['n']}. {s['title']}: FAIL {e}")
            problems += 1
            continue
        ctx = sc.make_context(s, target, other)
        ctx.setdefault("later_time", "18:15")      # what an agent's offer would fill in during a run
        ctx.setdefault("earlier_time", "17:45")
        brain = sc.CallerBrain(s, data["vocab"], ctx)
        notes: List[str] = []
        for m in s["moves"]:
            for mode in ("text", "tts"):
                say = m["say"]["text"] if mode == "text" else (m["say"].get("tts") or m["say"]["text"])
                try:
                    text = speech.fmt(say, ctx, s["language"], mode)
                except KeyError as e:
                    notes.append(f"{m['id']}: placeholder {e} has no value")
                    problems += 1
                    continue
                if mode != "tts":
                    continue
                req = when.resolve_request(re.sub(r"\d[\d \-]{6,}\d", " ", text), None)
                if m["id"] == "open" and "{day}" in say and req.date != target:
                    notes.append(f"open: the app reads the day as {req.date}, expected {target}")
                    problems += 1
                if m["id"] == "open" and "{time}" in say and s["target"].get("time") and \
                        (req.time.strftime("%H:%M") if req.time else None) != s["target"]["time"]:
                    notes.append(f"open: the app reads the time as {req.time}, expected {s['target']['time']}")
                    problems += 1
                if "{phone" in say or "{aadhaar" in say:
                    pipeline = caller_memory.extract(text).get("phone")
                    want = ctx["phone_slip"] if "{phone_slip" in say else ctx.get("phone")
                    if "{aadhaar" not in say and pipeline != want:
                        notes.append(f"{m['id']}: the app's caller-details rules read the phone as {pipeline!r}; "
                                     f"the scripted model reads it itself")
        log(f"  {s['n']}. {s['title']} [{s['language_label']}, {s['channel']['codec']}"
            f"{', ' + str(s['channel']['snr_db']) + ' dB' if s['channel'].get('snr_db') is not None else ''}] "
            f"{s['template']}, target {target} {s['target'].get('time') or str(nights) + ' nights'}: ok" +
            ("" if not notes else "\n       " + "\n       ".join(notes)))
        log(f"       opens: {brain.next_line(None).text}")
    log("dry run: " + ("no problems" if not problems else f"{problems} problem(s)"))
    return problems


# --------------------------------------------------------------------------------------------------- the stack

class Stack:
    """What the app is running on, read from its own API."""

    def __init__(self, api) -> None:
        r, _, _ = api.get("/api/health", timeout=15)
        health = r.json() if r.status_code == 200 else {}
        llm = health.get("llm") or {}
        e, _, _ = api.get("/api/engines", timeout=15)
        engines = e.json() if e.status_code == 200 else []
        timbre = next((x for x in engines if x.get("id") == "timbre"), {})
        self.timbre_in_app = bool(timbre.get("available"))
        self.llm_active = llm.get("active")
        self.llm_model = llm.get("model")
        try:
            from services import gnani
            self.key_here = bool(gnani.configured())
        except Exception:  # noqa: BLE001
            self.key_here = bool(os.getenv("GNANI_API_KEY"))
        # the audio run switches on by itself once the key exists (here, to speak, and in the app, to recognise and answer)
        self.audio = self.key_here and self.timbre_in_app

    @property
    def input(self) -> str:
        return "audio" if self.audio else "text"

    def describe(self) -> Dict[str, Any]:
        if self.audio:
            return {"input": "audio", "asr": "Gnani Prisma", "caller_voice": "Gnani Timbre",
                    "line": "8 kHz G.711 with the scenario's noise"}
        return {"input": "text", "asr": "not used in a text run", "caller_voice": "none (typed text)",
                "line": "not applied in a text run"}


class Voice:
    """The callers' voice: Gnani Timbre, cached on disk by text so a re-run is quick. Audio runs only."""

    def __init__(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def speak(self, text: str, lang: str, speed: float) -> Tuple[Any, str]:
        import numpy as np
        from services import gnani
        api_lang = {"en": "en", "hi": "hi", "mr": "mr", "hinglish": "hi-en"}[lang]
        key = audio.cache_key("timbre-v1", api_lang, text)
        path = CACHE_DIR / f"{key}.wav"
        if path.exists():
            x, srate = audio.read_wav_bytes(path.read_bytes())
        else:
            wav, srate = gnani.synth(text, api_lang)
            path.write_bytes(audio.wav_bytes(np.asarray(wav, dtype=np.float32), int(srate)))
            x = np.asarray(wav, dtype=np.float32)
        x16 = audio.with_lead(audio.slow_down(audio.to_16k(x, srate), speed))
        return x16.astype(np.float32), "Gnani Timbre"

    def pool(self, exclude: str, n: int = 6) -> List[Any]:
        out = []
        for f in sorted(CACHE_DIR.glob("*.wav")):
            if f.stem == exclude:
                continue
            with contextlib.suppress(Exception):
                x, s = audio.read_wav_bytes(f.read_bytes())
                out.append(audio.to_16k(x, s))
            if len(out) >= n:
                break
        return out


# ---------------------------------------------------------------------------------------------- one turn on the wire

def parse_sse(resp):
    """Yield (event, data dict, seconds since the request started) from a text/event-stream response."""
    ev = None
    t0 = time.perf_counter()
    for line in resp.iter_lines(decode_unicode=True):
        if line is None:
            continue
        if line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
            except ValueError:
                data = {"raw": line[5:].strip()}
            yield ev, data, time.perf_counter() - t0


def run_turn(api, agent_id: str, wav: bytes, conversation_id: Optional[str]) -> Dict[str, Any]:
    """Send one recording to the streaming turn endpoint and read everything it says."""
    form = {"test_call": "true"}
    if conversation_id:
        form["conversation_id"] = conversation_id
    t0 = time.perf_counter()
    resp = api.s.post(api.base + f"/api/conversations/{agent_id}/turn/stream", files={"audio": ("turn.wav", wav, "audio/wav")},
                      data=form, stream=True, timeout=(10, 300))
    if resp.status_code != 200:
        raise RuntimeError(f"turn endpoint answered {resp.status_code}: {resp.text[:300]}")
    out: Dict[str, Any] = {"sentences": [], "events": [], "done": None, "heard": None, "error": None, "first_sentence_s": None,
                           "first_answer_s": None}
    for ev, data, at in parse_sse(resp):
        if ev == "transcript":
            out["heard"] = data.get("user_text")
            out["heard_language"] = data.get("language")
            out["transcript_s"] = at
        elif ev == "sentence":
            out["sentences"].append({"index": data.get("index"), "text": data.get("text"), "filler": bool(data.get("filler")),
                                     "at_ms": int(at * 1000)})
            if out["first_sentence_s"] is None:
                out["first_sentence_s"] = at
        elif ev == "done":
            out["done"] = data
        elif ev == "error":
            out["error"] = data
        else:
            out["events"].append({"event": ev, "data": data, "at_ms": int(at * 1000)})
    out["wall_s"] = time.perf_counter() - t0
    return out


def run_text_turn(api, agent_id: str, text: str, conversation_id: Optional[str]) -> Dict[str, Any]:
    """One typed turn through the blocking turn endpoint (the same pipeline; no recognition, no caller voice)."""
    form = {"text": text, "test_call": "true"}
    if conversation_id:
        form["conversation_id"] = conversation_id
    t0 = time.perf_counter()
    r, _secs, _w = api.post(f"/api/conversations/{agent_id}/turn", data=form, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"turn endpoint answered {r.status_code}: {r.text[:300]}")
    done = r.json()
    t = done.get("timings") or {}
    # the reply text is ready before the voice has been rendered: the reply latency of a text run leaves the reply's speech out
    ready = max(0, int(done.get("total_ms") or 0) - int(t.get("tts_ms") or 0))
    return {"sentences": [], "events": [], "done": {**done, "timings": {**t, "first_answer_audio_ms": ready}},
            "heard": done.get("user_text"), "heard_language": done.get("language"), "error": None,
            "first_sentence_s": None, "wall_s": time.perf_counter() - t0}


# ------------------------------------------------------------------------------------------------ one scenario



def create_agent(api, s: Dict[str, Any]) -> Dict[str, Any]:
    r, _, _ = api.post("/api/agents/from-template", json={"template_id": s["template"], "org_name": s["org"], "city": s["city"],
                                                          "languages": s.get("agent_languages"), "include_sample": True}, timeout=600)
    if r.status_code != 200:
        raise RuntimeError(f"could not create the {s['template']} front desk ({r.status_code}): {r.text[:300]}")
    return r.json()


def api_booking(api, schedule_id: str, day, at: Optional[str], name: str, phone: str, party: Optional[int] = None,
                nights: Optional[int] = None) -> Dict[str, Any]:
    start = f"{day.isoformat()}T{at}" if at else day.isoformat()
    body: Dict[str, Any] = {"schedule_id": schedule_id, "start": start, "name": name, "phone": phone}
    if party:
        body["party"] = party
    if nights:
        body["nights"] = nights
    r, _, _ = api.post("/api/bookings", json=body, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"could not set up a booking ({r.status_code}): {r.text[:300]}")
    return r.json()["booking"]


def list_bookings(api, agent_id: str) -> List[Dict[str, Any]]:
    r, _, _ = api.get(f"/api/bookings?agent_id={agent_id}", timeout=60)
    return r.json().get("bookings", []) if r.status_code == 200 else []


def clean_up(api, agent_id: Optional[str], keep: bool) -> Dict[str, Any]:
    out = {"cancelled_bookings": 0, "agent_deleted": False}
    if not agent_id or keep:
        return out
    for b in list_bookings(api, agent_id):
        if b.get("status") == "booked":
            r, _, _ = api.delete(f"/api/bookings/{b['id']}", timeout=60)
            out["cancelled_bookings"] += 1 if r.status_code == 200 else 0
    r, _, _ = api.delete(f"/api/agents/{agent_id}", timeout=120)
    out["agent_deleted"] = r.status_code == 200
    return out


def run_scenario(api, stack: Stack, voice: Optional[Voice], s: Dict[str, Any], vocab: Dict[str, Any], run_dir: Path, run_id: str,
                 keep: bool = False) -> Dict[str, Any]:
    from datetime import date
    started = time.perf_counter()
    sched_spec = schedule_of(s)
    nights = s["target"].get("nights") or 1
    target, other = sc.pick_days(sched_spec, s["target"].get("time"), nights, date.today())
    ctx = sc.make_context(s, target, other)
    brain = sc.CallerBrain(s, vocab, ctx)
    audio_dir = run_dir / s["id"]
    rec: Dict[str, Any] = {
        "id": s["id"], "n": s["n"], "title": s["title"], "persona": s["persona"], "template": s["template"], "org": s["org"],
        "language": s["language"], "language_label": s["language_label"], "tags": s.get("tags", []),
        "channel": {"codec": s["channel"]["codec"], "snr_db": s["channel"].get("snr_db")},
        "dates": {"target": target.isoformat(), "other": other.isoformat()}, "turns": [], "setup": [], "error": None,
        "caller": {"name": s["caller"]["name"], "phone_masked": "••••" + re.sub(r"\D", "", s["caller"]["phone"])[-4:]},
        "expect": s["expect"], "agent_id": None, "conversation_id": None, "voice": None,
    }
    agent_id: Optional[str] = None
    before_ids: List[str] = []
    preset_ids: List[str] = []
    details: Any = None
    bookings_after: List[Dict[str, Any]] = []
    try:
        agent = create_agent(api, s)
        agent_id = rec["agent_id"] = agent["id"]
        schedule = (agent.get("schedules") or [None])[0]
        if not schedule:
            raise RuntimeError("the new front desk has no schedule")
        step = int(schedule.get("slot_minutes") or 30) + int(schedule.get("buffer_minutes") or 0)
        if s["target"].get("time"):
            h, m = speech.parse_hhmm(s["target"]["time"])
            total = h * 60 + m + step
            ctx["expected_later"] = f"{total // 60:02d}:{total % 60:02d}"
        for eb in s["setup"].get("existing_bookings", []):
            day = target if eb["date"] == "target" else other
            b = api_booking(api, schedule["id"], day, eb.get("time"), eb["name"], fill_phone(eb["phone"], ctx), eb.get("party"))
            preset_ids.append(b["id"])
            rec["setup"].append({"booking": b["id"], "date": day.isoformat(), "time": eb.get("time"), "name": eb["name"],
                                 "what": "already booked before the call"})
        before_ids = [b["id"] for b in list_bookings(api, agent_id)]
        speed = float((s.get("voice") or {}).get("speed", 1.0))
        rel = lambda p: str(Path(p).relative_to(RESULTS_DIR)) if p else None  # noqa: E731
        reply: Optional[str] = None
        conversation_id: Optional[str] = None
        for i in range(1, int(s["max_turns"]) + 1):
            line = brain.next_line(reply)
            if line is None:
                rec["ended"] = "the caller had nothing more to say"
                break
            caller_audio: Dict[str, Any] = {}
            if stack.audio:
                x16, label = voice.speak(line.tts, s["language"], speed)
                others = voice.pool(audio.cache_key("timbre-v1", {"en": "en", "hi": "hi", "mr": "mr", "hinglish": "hi-en"}[s["language"]], line.tts))
                degraded, dinfo = audio.degrade_line(x16, s["channel"], (s["id"], i, line.tts), others)
                clean_path = audio.encode_small(audio.wav_bytes(x16), audio_dir / f"t{i}-caller-clean.mp3")
                heard_path = audio.encode_small(audio.wav_bytes(degraded), audio_dir / f"t{i}-caller.mp3")
                rec["voice"] = rec["voice"] or label
                res = run_turn(api, agent_id, audio.wav_bytes(degraded), conversation_id)
                caller_audio = {"voice": label, "audio": rel(heard_path), "audio_clean": rel(clean_path),
                                "snr_db": dinfo.get("snr_db_measured"), "codec": dinfo["codec"]}
            else:
                res = run_text_turn(api, agent_id, line.text, conversation_id)
            if res["error"]:
                raise RuntimeError(f"turn {i} failed: {res['error']}")
            done = res["done"] or {}
            conversation_id = done.get("conversation_id") or conversation_id
            rec["conversation_id"] = conversation_id
            timings = done.get("timings") or {}
            agent_path = None
            if stack.audio and done.get("agent_audio_url"):
                ar, _, _ = api.get(done["agent_audio_url"], timeout=120)
                if ar.status_code == 200:
                    agent_path = audio.encode_small(ar.content, audio_dir / f"t{i}-agent.mp3")
            reply = done.get("agent_text") or " ".join(x["text"] for x in res["sentences"] if not x["filler"])
            first_answer = timings.get("first_answer_audio_ms")
            if first_answer is None and res["first_sentence_s"] is not None:
                first_answer = int(res["first_sentence_s"] * 1000)
            rec["turns"].append({
                "i": i,
                "caller": {"move": line.move, "text": line.text, "spoken": line.tts, "repeat": line.repeat, **caller_audio},
                "heard": res["heard"], "heard_language": res.get("heard_language"),
                "agent": {"text": reply, "audio": rel(agent_path), "sentences": [x for x in res["sentences"]],
                          "tool_calls": done.get("tool_calls") or [], "language": done.get("language"),
                          "llm_model": done.get("llm_model"), "fallback": done.get("fallback"),
                          "canned": bool(done.get("canned_reply")), "extra_events": res["events"]},
                "latency": {"first_audio_ms": timings.get("first_audio_ms"), "first_answer_ms": first_answer,
                            "asr_ms": timings.get("asr_ms"), "llm_first_token_ms": timings.get("llm_first_token_ms"),
                            "tts_ms": timings.get("tts_ms"), "total_ms": timings.get("total_ms")},
                "wall_ms": int(res["wall_s"] * 1000),
            })
            log(f"    turn {i} [{line.move}{' again' if line.repeat else ''}] caller: {line.text[:70]}")
            log(f"           heard: {(res['heard'] or '')[:70]}")
            log(f"           agent: {(reply or '')[:90]}  tools={[(c['name'], c['ok']) for c in (done.get('tool_calls') or [])]}")
            stop = s["expect"].get("stop_after")
            made = any(c.get("ok") and str(c.get("name", "")).rsplit(".", 1)[-1] == ("cancel" if stop == "cancel" else "book")
                       for c in (done.get("tool_calls") or []))
            if stop and made:
                rec["ended"] = "the booking was made" if stop == "booking" else "the booking was cancelled"
                break
        else:
            rec["ended"] = "the turn limit was reached"
        bookings_after = list_bookings(api, agent_id)
        if conversation_id:
            r, _, _ = api.get(f"/api/calls/{conversation_id}", timeout=60)
            if r.status_code == 200:
                details = r.json().get("details")
        rec["bookings"] = [{k: b.get(k) for k in ("id", "name", "phone", "schedule_name", "date", "time", "nights", "party", "status",
                                                    "note", "source")} for b in bookings_after]
    except Exception as e:  # noqa: BLE001 - a scenario that cannot run is a failed scenario, said plainly
        rec["error"] = f"{type(e).__name__}: {e}"
        log(f"    ERROR {rec['error']}")
    finally:
        try:
            rec["cleanup"] = clean_up(api, agent_id, keep)
        except Exception as e:  # noqa: BLE001
            rec["cleanup"] = {"error": f"{type(e).__name__}: {e}"}
    models = {(t["agent"].get("llm_model") or "") for t in rec["turns"] if t["agent"].get("llm_model")}
    rec["llm_models"] = sorted(models)
    rec["verdict"] = scorer.score(s, ctx, rec, bookings_after, before_ids, preset_ids, details) if not rec["error"] else {
        "pass": False, "reason": "The scenario could not run: " + rec["error"], "checks": [],
        "metrics": {"fields_correct": 0, "fields_total": 0, "fields_correct_pct": None, "turns_to_complete": None,
                    "caller_turns": len(rec["turns"]), "read_back_before_commit": None, "readback_catch": None,
                    "first_reply_ms": None, "total_ms": 0}}
    rec["seconds"] = round(time.perf_counter() - started, 1)
    log(f"  => {'PASS' if rec['verdict']['pass'] else 'FAIL'}: {rec['verdict']['reason']}")
    return rec


# ------------------------------------------------------------------------------------------------------ the run

def mode_of(results: List[Dict[str, Any]], forced: Optional[str]) -> str:
    if forced:
        return forced
    models = {m for r in results for m in r.get("llm_models", [])}
    return "scripted" if models and models <= {SCRIPTED_MODEL} else "live"


INPUT_LABEL = {"audio": "audio run: Gnani Timbre caller voices", "text": "text run (audio run pending)"}


def run_all(args) -> Dict[str, Any]:
    from common import Api, require_api
    api = Api(args.api)
    require_api(api)
    stack = Stack(api)
    if args.text:
        stack.audio = False
    if args.scripted_llm:
        import scripted_llm
        scripted_llm.serve(args.scripted_port)
        log(f"scripted model serving on 127.0.0.1:{args.scripted_port}")
        if stack.llm_model != SCRIPTED_MODEL:
            raise SystemExit("The app is not using the scripted model. Restart it with\n"
                             f"  LLM_BASE_URL=http://127.0.0.1:{args.scripted_port}/v1 LLM_API_KEY=theatre LLM_MODEL=theatre-scripted ./vakya start\n"
                             "and run this again.")
    elif not args.allow_live:
        raise SystemExit("Run with --scripted-llm (labelled 'scripted model'), or --allow-live once a hosted model is configured.")
    data = sc.load()
    chosen = [s for s in data["scenarios"] if not args.only or s["n"] in args.only]
    voice = Voice() if stack.audio else None
    stamp = now_stamp()
    run_dir = RESULTS_DIR / "audio" / stamp
    log(f"Caller Theatre {stamp}: {len(chosen)} scenarios against {api.base}; {INPUT_LABEL[stack.input]}")
    results = []
    for s in chosen:
        log(f"[{s['n']}] {s['title']}  ({s['language_label']}, {s['template']})")
        results.append(run_scenario(api, stack, voice, s, data["vocab"], run_dir, stamp, keep=args.keep))
    mode = mode_of(results, args.mode)
    out = {
        "schema": 2, "run_id": stamp, "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": mode, "input": stack.input, "input_label": INPUT_LABEL[stack.input],
        "mode_label": ("Scripted model: the tools, bookings and rules are the real app; the model's wording and choice of tool come "
                       "from a deterministic stand-in" if mode == "scripted" else "Live model: the app's configured hosted model"),
        "label": ("Synthetic callers, voices by Gnani Timbre, scored automatically" if stack.audio else
                  "Synthetic callers, text run (audio run pending), scored automatically"),
        "stack": stack.describe(), "host": {"platform": platform.platform(), "python": platform.python_version()},
        "scenarios": results, "summary": scorer.summarise(results),
        "limits": ["Every caller is synthetic and scripted in advance; no real caller was recorded.",
                   "One run per scenario: a pass shows the path works, not how often.",
                   ("Callers are Gnani Timbre voices on an 8 kHz G.711 line with the scenario's noise; the SNR shown is measured on the mix."
                    if stack.audio else
                    "A text run sends the callers' words as typed text: speech recognition, caller voices and line noise are not exercised, "
                    "so the noise levels shown are planned, not applied. The audio run switches on by itself once GNANI_API_KEY is set.")],
    }
    return out


def save(result: Dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{result['run_id']}-{result['mode']}-{result['input']}.json"
    text = json.dumps(result, ensure_ascii=False, indent=1)
    path.write_text(text, encoding="utf-8")
    (RESULTS_DIR / "latest.json").write_text(text, encoding="utf-8")
    (RESULTS_DIR / f"latest-{result['mode']}.json").write_text(text, encoding="utf-8")
    return path


def markdown_table(result: Dict[str, Any]) -> str:
    s = result["summary"]
    scripted = result["mode"] == "scripted"   # the stand-in model answers instantly, so its timings say nothing about a real model
    if scripted:
        timing = "first reply and total time not measured (scripted model, latency not meaningful: it answers instantly)"
    else:
        timing = "median first reply " + ("n/a" if s["median_first_reply_ms"] is None else str(round(s["median_first_reply_ms"] / 1000, 1)) + " s")
    lines = [f"**{result['mode'].capitalize()} model, {result['input_label']}, run {result['run_id']}.** {result['mode_label']}. "
             f"{s['passed']} of {s['scenarios']} passed; fields correct {s['fields_correct']}/{s['fields_total']}"
             f" ({s['fields_correct_pct']}%); {timing}.", "",
             "| # | Scenario | Language | Line | Result | Fields | Turns | First reply | Total | Why |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in result["scenarios"]:
        m = r["verdict"]["metrics"]
        noise = f"{r['channel']['snr_db']} dB" if r["channel"].get("snr_db") is not None else "clean"
        applied = "" if result["input"] == "audio" else " (planned)"
        fr = "n/m" if scripted else "n/a" if m.get("first_reply_ms") is None else f"{m['first_reply_ms'] / 1000:.1f} s"
        total = "n/m" if scripted else f"{m['total_ms'] / 1000:.0f} s"
        lines.append(f"| {r['n']} | {r['title']} | {r['language_label']} | 8 kHz G.711, {noise}{applied} | "
                     f"{'PASS' if r['verdict']['pass'] else 'FAIL'} | {m['fields_correct']}/{m['fields_total']} | "
                     f"{m['turns_to_complete'] or '-'} / {m['caller_turns']} | {fr} | {total} | "
                     f"{r['verdict']['reason'].replace('|', '/')} |")
    return "\n".join(lines)


def update_docs(latest_by_mode: Dict[str, Dict[str, Any]]) -> None:
    if not DOC_PATH.exists():
        return
    text = DOC_PATH.read_text(encoding="utf-8")
    if DOC_BEGIN not in text or DOC_END not in text:
        return
    body = "\n\n".join(markdown_table(latest_by_mode[m]) for m in ("scripted", "live") if m in latest_by_mode) or "No run yet."
    pre, rest = text.split(DOC_BEGIN, 1)
    _old, post = rest.split(DOC_END, 1)
    DOC_PATH.write_text(pre + DOC_BEGIN + "\n" + body + "\n" + DOC_END + post, encoding="utf-8")


def load_latest_by_mode() -> Dict[str, Dict[str, Any]]:
    out = {}
    for mode in ("scripted", "live"):
        p = RESULTS_DIR / f"latest-{mode}.json"
        if p.exists():
            with contextlib.suppress(Exception):
                out[mode] = json.loads(p.read_text(encoding="utf-8"))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--api", default=os.getenv("VAKYA_API", "http://127.0.0.1:8020"))
    ap.add_argument("--only", help="scenario numbers to run, e.g. 1,5")
    ap.add_argument("--dry-run", action="store_true", help="validate the scenarios and show what the app reads from each line; call nothing")
    ap.add_argument("--scripted-llm", action="store_true", help="serve the deterministic stand-in model and label the run 'scripted'")
    ap.add_argument("--scripted-port", type=int, default=8099)
    ap.add_argument("--mode", choices=("live", "scripted"), help="force the label (default: read from the model the app answered with)")
    ap.add_argument("--text", action="store_true", help="force a text run even when GNANI_API_KEY is set")
    ap.add_argument("--allow-live", action="store_true", help="run against the app's configured model (a hosted one) and label it live")
    ap.add_argument("--keep", action="store_true", help="do not delete the front desks and bookings afterwards")
    ap.add_argument("--no-docs", action="store_true", help="do not rewrite the table in docs/THEATRE.md")
    a = ap.parse_args(argv)
    a.only = [int(x) for x in a.only.split(",")] if a.only else None
    if a.dry_run:
        return 1 if dry_run(sc.load(), a.only) else 0
    result = run_all(a)
    path = save(result)
    if not a.no_docs and not a.only:
        update_docs(load_latest_by_mode())
    s = result["summary"]
    log(f"\n{result['mode']}: {s['passed']} of {s['scenarios']} passed, fields {s['fields_correct']}/{s['fields_total']}; saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
