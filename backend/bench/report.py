#!/usr/bin/env python3
"""Write docs/RESULTS.md from the result files in backend/bench/results/.

Each section uses the fullest real run of its kind (most turns, runs or soak minutes; the newest among equals), so a
later one-question check or short soak does not replace the full measurement. Quick smoke runs are used only when
nothing else exists.

    backend/.venv/bin/python backend/bench/report.py
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

from common import REPO_ROOT, RESULTS_DIR, fmt

KINDS = ["latency", "latency_stream", "soak"]
RUN_CMD = {
    "latency": "backend/.venv/bin/python backend/bench/latency.py",
    "latency_stream": "backend/.venv/bin/python backend/bench/latency.py --stream",
    "soak": "backend/.venv/bin/python backend/bench/soak.py --minutes 45",
}


def _size(kind: str, res: dict) -> float:
    """How complete a run is: more turns, runs or minutes is a fuller measurement."""
    summ, settings = res.get("summary") or {}, res.get("settings") or {}
    if kind in ("latency", "latency_stream"):
        return summ.get("measured_turns") or 0
    if kind == "soak":
        return settings.get("minutes") or 0
    return 0


def _load_all(kind: str):
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(RESULTS_DIR.glob(f"{kind}-*.json"))]


def pick(kind: str):
    """The fullest real run of this kind (newest among equals); a quick smoke run only if there is nothing else."""
    runs = _load_all(kind)
    if not runs:
        return None
    real = [r for r in runs if not r.get("quick")] or runs
    return max(enumerate(real), key=lambda ir: (_size(kind, ir[1]), ir[0]))[1]


def row(cells):
    return "| " + " | ".join(str(c) for c in cells) + " |"


def table(header, rows):
    return "\n".join([row(header), row(["---"] * len(header))] + [row(r) for r in rows])


def run_line(res):
    g = res["git"]
    q = " **Quick smoke run: not a real measurement.**" if res.get("quick") else ""
    llm = (res["health"].get("llm") or {})
    return (f"Run {res['timestamp']}, commit {g['commit']}{' (uncommitted changes)' if g['dirty_working_tree'] else ''}, "
            f"LLM: {llm.get('active')} {llm.get('model')}.{q}")


def not_measured(kind):
    return f"Not measured. Run `{RUN_CMD[kind]}` and then this report again."


def sec_latency(res):
    if not res:
        return not_measured("latency")
    s = res["summary"]
    rows = []
    for name, b in [("all", s["overall"]), ("English", s["by_language"]["en"]), ("Hindi", s["by_language"]["hi"])]:
        if not b["n"]:
            rows.append([name, 0] + ["not measured"] * 11)
            continue
        c, v, st = b["client_ms"], b["server_ms"], b["stage_p50_ms"]
        rows.append([name, b["n"], fmt(c["p50"]), fmt(c["p95"]), fmt(v["p50"]), fmt(v["p95"]),
                     f"{fmt(b['pct_client_within_3s'])}%", fmt(st["asr_ms"]), fmt(st["retrieval_ms"]),
                     fmt(st["llm_ms"]), fmt(st["tts_ms"]), b["fallback_replies"]])
    t = table(["Questions", "Turns", "Client p50 (ms)", "Client p95 (ms)", "Server p50 (ms)", "Server p95 (ms)",
               "Within 3 s", "ASR p50", "Retrieval p50", "LLM p50", "TTS p50", "Fallback replies"], rows)
    st = res["settings"]
    return (f"Method: {st['turns']} spoken questions ({st['turns'] // 2 + st['turns'] % 2} English, "
            f"{st['turns'] // 2} Hindi, answerable from the clinic receptionist template) rendered with Gnani Timbre "
            f"beforehand, posted one by one to `/api/conversations/{{agent}}/turn`"
            f"{' in one conversation' if st['same_conversation'] else ', a new conversation each'}, after "
            f"{st['warmup']} warm-up turn(s). Client time is request start until the JSON is received; server time is "
            "the API's own `total_ms`. This is speech in to the full reply audio ready: replies are not streamed, so "
            f"it is not time to first audio. Failed turns: {s['failed_turns']}.\n\n{run_line(res)}\n\n{t}")


def sec_stream(res):
    if not res:
        return not_measured("latency_stream")
    s = res["summary"]
    rows = []
    for name, b in [("all", s["overall"]), ("English", s["by_language"]["en"]), ("Hindi", s["by_language"]["hi"])]:
        if not b["n"]:
            rows.append([name, 0] + ["not measured"] * 12)
            continue
        f, a, d, sv = (b["client_first_audio_ms"], b["client_first_answer_ms"], b["client_full_reply_ms"],
                       b["server_first_audio_ms"])
        st = b["stage_p50_ms"]
        rows.append([name, b["n"], fmt(f["p50"]), fmt(f["p95"]), f"{fmt(b['pct_first_audio_within_3s'])}%",
                     fmt(a["p50"]), fmt(a["p95"]), f"{fmt(b['pct_first_answer_within_3s'])}%", fmt(d["p50"]),
                     fmt(d["p95"]), fmt(sv["p50"]), fmt(st["asr_ms"]), fmt(st["llm_first_token_ms"]),
                     f"{b['disclosed_ai']}/{b['n']}"])
    t = table(["Questions", "Turns", "First audio p50 (ms)", "First audio p95 (ms)", "First audio within 3 s",
               "First answer p50 (ms)", "First answer p95 (ms)", "First answer within 3 s", "Full reply p50 (ms)",
               "Full reply p95 (ms)", "Server first audio p50", "ASR p50", "LLM first token p50",
               "AI disclosure spoken"], rows)
    st = res["settings"]
    disclosure = ("The agent's AI disclosure was switched off for this run, so the first sentence is the answer."
                  if st.get("no_disclosure") else
                  "The agent says it is an AI at the start of each call (each question is a new conversation), so "
                  "its first sentence is the spoken disclosure; it is synthesized once and replayed, and plays while the "
                  "model is still writing the answer. \"First answer\" is the first sentence of the answer itself.")
    return (f"Method: the same {st['turns']} questions as above, posted to `/api/conversations/{{agent}}/turn/stream`, "
            f"which answers with server-sent events{' in one conversation' if st['same_conversation'] else ', a new '
            'conversation each'}, after {st['warmup']} warm-up turn(s). The client clock starts when the request is "
            "sent and stops when the first `sentence` event arrives (first audio: the caller can start hearing the "
            "reply), when the first sentence of the answer arrives, "
            "and when `done` arrives (the whole reply ready, same as the table above). "
            f"{disclosure} Failed turns: {s['failed_turns']}.\n\n{run_line(res)}\n\n{t}")


def sec_soak(res):
    if not res:
        return not_measured("soak")
    s = res["summary"]
    mem = s["memory"]
    mrows = []
    for k, label in [("api_rss", "API process"), ("children_rss", "Child processes"),
                     ("total_rss", "Total")]:
        m = mem.get(k)
        mrows.append([label] + ([f"{m['start_mb']:.0f}", f"{m['end_mb']:.0f}", f"{m['max_mb']:.0f}",
                                 f"{m['slope_mb_per_min']:+.2f}"] if m else ["not measured"] * 4))
    lrows = [[k, v["n"], fmt(v["p50"]), fmt(v["p95"])] for k, v in s["latency_ms"].items()]
    st = res["settings"]
    fr = "n/a" if s["failure_rate"] is None else f"{s['failure_rate'] * 100:.2f}%"
    summary = table(["Measure", "Result"], [
        ["Duration", f"{(s['elapsed_s'] or 0) / 60:.1f} min (planned {st['minutes']:g})"],
        ["Requests", f"{s['requests']} ({s['ok']} OK)"],
        ["Failure rate", f"{fr} ({s['failures']} failures; by cause: {s['failures_by_cause'] or 'none'})"],
        ["Crashes / restarts (target 0)", s["crashes"]],
        ["Health-check failures", s["health_failures"]],
    ])
    return (f"Method: a weighted mix of Timbre English and Hindi generation, spoken agent turns, transcription"
            f"{' and cloned-voice generation' if st.get('voice_id') else ''}, one request at a time with short "
            "random pauses; process memory and `/api/health` sampled every "
            f"{st['sample_interval_s']:g} s. A crash is the API becoming unreachable and coming back, or its process "
            f"id changing. Requests answered 429 by the API's rate limiter ({s['rate_limited_429']}) are not "
            f"counted as failures.\n\n{run_line(res)}\n\n{summary}\n\nLatency by request type:\n\n"
            f"{table(['Request', 'n', 'p50 (ms)', 'p95 (ms)'], lrows)}\n\nMemory (RSS, MB; trend is a linear fit):\n\n"
            f"{table(['', 'Start', 'End', 'Max', 'MB per minute'], mrows)}")


def reference_points(lat, soak, stream=None):
    c = lat["summary"]["overall"]["client_ms"]["p50"] if lat and lat["summary"]["overall"]["n"] else None
    mine_lat = "not measured" if c is None else f"p50 {c / 1000:.1f} s (client)"
    sb = stream["summary"]["overall"] if stream and stream["summary"]["overall"]["n"] else None
    if sb:
        f, a = sb["client_first_audio_ms"]["p50"], sb["client_first_answer_ms"]["p50"]
        mine_stream = (f"first audio p50 {f / 1000:.1f} s, first sentence of the answer p50 {a / 1000:.1f} s "
                       "(client, streaming)")
    else:
        mine_stream = "not measured (run latency.py --stream)"
    if soak:
        s = soak["summary"]
        mins = (s['elapsed_s'] or 0) / 60
        mine_soak = f"{s['crashes']} crash(es) in {mins:.0f} min" + ("" if mins >= 45 else " (shorter than the 45 min target)")
    else:
        mine_soak = "not measured"
    return table(["Reference point", "Target / published figure", "Vakya (this report)"], [
        ["Spoken reply time", "under 3 s from the end of the question to the reply being ready (the plan's target)",
         mine_lat],
        ["Twilio, mouth-to-ear", "1,115 ms median, as published by Twilio (the plan's yardstick; not re-measured "
         "here). It is a different measurement from this report's full-reply time, so treat it as context, not a "
         "like-for-like comparison",
         f"not like-for-like: {mine_stream}. Both start the clock when the audio request is sent, not when the caller "
         "stops speaking, and Vakya adds speech recognition and language model time that a telephony figure does not include"],
        ["Stability", "45 minutes of mixed load without a crash", mine_soak],
    ])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO_ROOT / "docs" / "RESULTS.md"))
    a = ap.parse_args()
    res = {k: pick(k) for k in KINDS}
    present = [r for r in res.values() if r]
    if not present:
        raise SystemExit(f"No result files in {RESULTS_DIR}. Run the scripts in backend/bench first.")
    ref = max(present, key=lambda r: r["timestamp"])
    m, g = ref["machine"], ref["git"]
    date = datetime.fromisoformat(ref["timestamp"]).strftime("%d %B %Y")
    quick = [k for k, r in res.items() if r and r.get("quick")]
    commits = sorted({r["git"]["commit"] for r in present})
    intro = (f"Measured on {m['cpu']} ({m['cpu_count']} logical cores), {m['ram_gb']:g} GB RAM, {m['platform']}; "
             f"{date}; commit {g['commit']}"
             f"{'' if len(commits) == 1 else ' (newest; the tables below name the commit of each run: ' + ', '.join(commits) + ')'}. "
             "Every number here comes from the scripts in `backend/bench/`, which call the running API over HTTP, "
             "so they measure the real system. Rerun everything with:\n\n```\n./vakya start\n" +
             "\n".join(RUN_CMD[k] for k in KINDS) + "\nbackend/.venv/bin/python backend/bench/report.py\n```\n\n"
             "Raw per-item measurements are in `backend/bench/results/` (not committed). Anything not measured is "
             "marked \"not measured\".")
    if quick:
        intro += f"\n\n> Warning: these results include quick smoke runs ({', '.join(quick)}), not real measurements."
    doc = "\n\n".join([
        "# Vakya measured results", intro,
        "## Spoken-turn latency", sec_latency(res["latency"]),
        "## Time to first audio (streaming)", sec_stream(res["latency_stream"]),
        "## Stability soak", sec_soak(res["soak"]),
        "## Reference points", reference_points(res["latency"], res["soak"], res["latency_stream"]),
    ]) + "\n"
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
