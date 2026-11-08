#!/usr/bin/env python3
"""The cross-machine benchmark: one command, the same steps everywhere, one JSON and one Markdown table to compare.

Runs the existing measurement scripts with fixed settings against the running API (start it first; warm it too), and
samples memory the whole time: system RAM and the app's own view (GET /api/system/resources). The speech and the language
model are hosted (Gnani and the hosted models), so what differs between machines is the network, the CPU and the app itself. Steps:

  talk        latency.py --stream -n 20         spoken question in, time to first audio / first answer / full reply
  phone       phone_latency.py --runs 5         the phone WebSocket with a preset voice (no real call is placed)
  phone_clone phone_latency.py --runs 5         the same with a voice cloned through Gnani on the agent
                                                (needs PHONE_CLONE_VOICE=1 in backend/.env)
  retrieval   retrieval_eval.py                 offline knowledge-search accuracy and query time
  tests       pytest + jest (only with --tests) pass / fail counts and duration

Every spoken input is a synthetic Gnani Timbre voice (this spends Gnani credits); nothing real is called; the agent and
voices it creates are deleted.

    backend/.venv/bin/python backend/bench/suite.py --label mac-m4 --tests
    backend\\.venv\\Scripts\\python backend\\bench\\suite.py --label win-4070 --tests --compare <earlier-run>.json
    backend/.venv/bin/python backend/bench/suite.py --compare A.json B.json      # just print the comparison

Writes backend/bench/results/suite-<label>-<stamp>.json and .md.
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import (BENCH_DIR, REPO_ROOT, RESULTS_DIR, Api, err_text, git_info, machine_info, require_api, tts_wav)

BACKEND = BENCH_DIR.parent
PY = sys.executable
IS_WIN = os.name == "nt"


# ------------------------------------------------------------------------------------------------------ memory

class Sampler:
    """Peaks over one step: system RAM used and the app's own resident memory."""

    def __init__(self, api: Api):
        self.api, self.stop, self.peak = api, threading.Event(), {}

    def _max(self, key: str, value: Optional[float]) -> None:
        if value is not None:
            self.peak[key] = max(self.peak.get(key, 0), round(value))

    def _run(self) -> None:
        import psutil
        last_app = 0.0
        while not self.stop.is_set():
            self._max("system_used_mb", (psutil.virtual_memory().total - psutil.virtual_memory().available) / 2 ** 20)
            if time.time() - last_app > 2:
                last_app = time.time()
                try:
                    r, _, _ = self.api.get("/api/system/resources", timeout=5)
                    j = r.json() if r.status_code == 200 else {}
                    self._max("app_total_rss_mb", (j.get("app") or {}).get("total_rss_mb"))
                except Exception:  # noqa: BLE001
                    pass
            self.stop.wait(1.0)

    def __enter__(self):
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.t.join(timeout=10)


# ------------------------------------------------------------------------------------------------------ steps

def newest(prefix: str, since: float) -> Optional[Path]:
    files = [p for p in RESULTS_DIR.glob(f"{prefix}-*.json") if p.stat().st_mtime >= since]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def run(cmd: List[str], log: Path, cwd: Path = BACKEND, timeout: int = 3600) -> Dict[str, Any]:
    t0 = time.time()
    with open(log, "w", encoding="utf-8") as fh:
        try:
            p = subprocess.run(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout, shell=False,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            code = p.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
        except FileNotFoundError as e:
            fh.write(str(e))
            code = "not found"
    root = str(REPO_ROOT) + os.sep
    tail = [l.replace(root, "") for l in log.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]]
    return {"exit": code, "wall_s": round(time.time() - t0, 1), "log": str(log.relative_to(REPO_ROOT)), "tail": tail}


def script(name: str, *args: str) -> List[str]:
    return [PY, str(BENCH_DIR / name), *args]


CLONE_REFERENCE_PASSAGE = ("The morning light came slowly over the quiet hills, and a gentle wind moved through the tall "
                           "grass beside the river. Birds began to sing.")
FALLBACK_CONSENT = "I consent to creating an AI version of my voice."  # the statement the API requires verbatim


def fetch_challenge(api: Api):
    """(challenge_id, text) from GET /api/consent/challenge when the API has it, else (None, None)."""
    try:
        r, _, _ = api.get("/api/consent/challenge", timeout=15)
        j = r.json() if r.status_code == 200 else {}
    except Exception:  # noqa: BLE001
        return None, None
    if isinstance(j.get("challenge"), dict):
        j = j["challenge"]
    text = next((j[k] for k in ("challenge_text", "text", "phrase", "challenge", "consent_text", "statement", "sentence")
                 if isinstance(j.get(k), str) and j[k].strip()), None)
    cid = next((j[k] for k in ("challenge_id", "id") if j.get(k)), None)
    return (str(cid) if cid else None), text


def make_clone(api: Api) -> str:
    """A cloned voice from a synthetic Timbre voice reading a neutral passage and the consent sentence."""
    ref, _ = tts_wav(api, "en", CLONE_REFERENCE_PASSAGE)
    cid, ctext = fetch_challenge(api)
    ctext = ctext or FALLBACK_CONSENT
    consent, _ = tts_wav(api, "en", ctext)
    r, _, _ = api.post("/api/voices/clone", files={"reference_audio": ("r.wav", ref, "audio/wav"),
                                                   "consent_audio": ("c.wav", consent, "audio/wav")},
                       data={"display_name": "bench-suite-clone", "language": "en", "consent_confirmed": "true",
                             "consent_text": ctext, **({"challenge_id": cid} if cid else {})}, timeout=900)
    if r.status_code != 200:
        raise RuntimeError(f"clone failed: HTTP {r.status_code} {err_text(r)}")
    return r.json()["id"]


def parse_pytest(tail: List[str]) -> Dict[str, Any]:
    line = next((l for l in reversed(tail) if re.search(r"\d+ (passed|failed)", l)), "")
    return {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed)", line)}


def parse_jest(text: str) -> Dict[str, Any]:
    out = {}
    for kind in ("Test Suites", "Tests"):
        m = re.search(rf"{kind}:\s+(.*)", text)
        if m:
            out[kind.lower().replace(" ", "_")] = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|skipped|total)", m.group(1))}
    return out


def phone_env() -> Optional[str]:
    try:
        from dotenv import dotenv_values
        return dotenv_values(BACKEND / ".env").get("PHONE_CLONE_VOICE")  # only this key is read
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------------------------------------------ headline

def g(d: Any, *path: str) -> Any:
    for k in path:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def headline(steps: Dict[str, Any]) -> Dict[str, Any]:
    """The flat list of numbers the comparison table shows (ms and s are lower-is-better unless noted)."""
    s = {k: v.get("summary") or {} for k, v in steps.items()}
    o = g(s, "talk", "overall") or {}
    rt = s.get("retrieval", {})
    h = {
        "talk: first audio p50 (ms)": g(o, "client_first_audio_ms", "p50"),
        "talk: first audio p95 (ms)": g(o, "client_first_audio_ms", "p95"),
        "talk: first answer p50 (ms)": g(o, "client_first_answer_ms", "p50"),
        "talk: full reply p50 (ms)": g(o, "client_full_reply_ms", "p50"),
        "talk: ASR p50 (ms)": g(o, "stage_p50_ms", "asr_ms"),
        "talk: retrieval p50 (ms)": g(o, "stage_p50_ms", "retrieval_ms"),
        "talk: LLM first token p50 (ms)": g(o, "stage_p50_ms", "llm_first_token_ms"),
        "talk: TTS p50 (ms)": g(o, "stage_p50_ms", "tts_ms"),
        "talk: failed turns": g(s, "talk", "failed_turns"),
        "phone preset: greeting (ms)": g(s, "phone", "greeting_ms_median"),
        "phone preset: end of speech to reply (ms)": g(s, "phone", "reply_ms_median"),
        "phone clone: greeting (ms)": g(s, "phone_clone", "greeting_ms_median"),
        "phone clone: end of speech to reply (ms)": g(s, "phone_clone", "reply_ms_median"),
    }
    for name, r in (rt.get("results") or {}).items():
        if isinstance(r, dict):
            h[f"retrieval {name}: hit@1 (higher better)"] = r.get("hit@1")
    for name, t in (rt.get("timings") or {}).items():
        h[f"retrieval {name}: query encode p50 (ms)"] = t.get("query_encode_ms_median")
    tp, tj = g(steps, "tests", "pytest") or {}, g(steps, "tests", "jest") or {}
    if tp:
        h["pytest passed (higher better)"], h["pytest failed"] = tp.get("passed"), tp.get("failed", 0)
    if tj:
        h["jest tests passed (higher better)"] = g(tj, "tests", "passed")
        h["jest tests failed"] = g(tj, "tests", "failed") or 0
    for k, v in steps.items():
        if v.get("peak"):
            for pk in ("app_total_rss_mb", "system_used_mb"):
                if pk in v["peak"]:
                    h[f"peak {pk} during {k}"] = v["peak"][pk]
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in h.items() if v is not None}


def markdown(a: dict, b: Optional[dict] = None) -> str:
    def who(x):
        m = x["machine"]
        return (f"{x['label']}: {m.get('cpu')}, {m.get('ram_gb')} GB RAM, "
                f"{m.get('platform')}, commit {x['git'].get('commit')}{' (dirty)' if x['git'].get('dirty_working_tree') else ''}, LLM {x.get('llm')}")
    lines = [f"# Benchmark suite: {a['label']}" + (f" vs {b['label']}" if b else ""), "", f"- {who(a)}"]
    if b:
        lines += [f"- {who(b)}", "", f"| Metric | {a['label']} | {b['label']} | {b['label']} / {a['label']} |",
                  "| --- | ---: | ---: | ---: |"]
        for k in list(dict.fromkeys([*a["headline"], *b["headline"]])):
            x, y = a["headline"].get(k), b["headline"].get(k)
            ratio = f"{y / x:.2f}x" if isinstance(x, (int, float)) and isinstance(y, (int, float)) and x else ""
            lines.append(f"| {k} | {'' if x is None else x} | {'' if y is None else y} | {ratio} |")
    else:
        lines += ["", "| Metric | Value |", "| --- | ---: |"] + [f"| {k} | {v} |" for k, v in a["headline"].items()]
    lines += ["", "Step status:" if not b else "Step status (second machine):"]
    for k, v in (b or a)["steps"].items():
        lines.append(f"- {k}: exit {v.get('exit')}, {v.get('wall_s')} s" + (f", {v['error']}" if v.get("error") else ""))
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------------------ main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=os.getenv("VAKYA_API", "http://127.0.0.1:8020"))
    ap.add_argument("--label", default=("win" if IS_WIN else sys.platform))
    ap.add_argument("--only", help="comma list of steps (talk,phone,phone_clone,retrieval,tests)")
    ap.add_argument("--tests", action="store_true", help="also run the backend pytest and the frontend jest suites")
    ap.add_argument("--quick", action="store_true", help="smoke run, numbers are meaningless")
    ap.add_argument("--compare", nargs="+", metavar="JSON", help="baseline suite JSON (with two files: only compare)")
    a = ap.parse_args()

    if a.compare and len(a.compare) == 2:
        x, y = (json.loads(Path(p).read_text(encoding="utf-8")) for p in a.compare)
        print(markdown(x, y))
        return

    api = Api(a.api)
    health = require_api(api)
    steps_wanted = (a.only.split(",") if a.only else
                    ["talk", "phone", "phone_clone", "retrieval"] + (["tests"] if a.tests else []))
    q = a.quick
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    logs = RESULTS_DIR / f"suite-{a.label}-{stamp}-logs"
    logs.mkdir(parents=True, exist_ok=True)
    res: Dict[str, Any] = {"name": "suite", "label": a.label, "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
                           "git": git_info(), "machine": machine_info(),
                           "health_platform": health.get("platform"),
                           "llm": f"{g(health, 'llm', 'active')} {g(health, 'llm', 'model')}",
                           "phone_clone_voice_env": phone_env(), "quick": q, "steps": {}}
    try:
        r, _, _ = api.get("/api/system/resources", timeout=10)
        res["resources_at_start"] = (r.json() if r.status_code == 200 else None)
    except Exception:  # noqa: BLE001
        res["resources_at_start"] = None

    agent_id = clone_id = None
    plans = {
        "talk": ("latency_stream", lambda: script("latency.py", "--stream", "-n", "3" if q else "20")),
        "phone": ("phone_latency", lambda: script("phone_latency.py", "--agent", agent_id, "--runs", "2" if q else "5")),
        "phone_clone": ("phone_latency", lambda: script("phone_latency.py", "--agent", agent_id, "--runs", "2" if q else "5")),
        "retrieval": ("retrieval_eval", lambda: script("retrieval_eval.py", *(["--quick"] if q else []))),
    }
    try:
        for step in steps_wanted:
            print(f"\n--- {step} ---", flush=True)
            entry: Dict[str, Any] = {}
            t0 = time.time()
            try:
                if step in ("phone", "phone_clone") and not agent_id:
                    r, _, _ = api.post("/api/agents/from-template", json={"template_id": "clinic"}, timeout=300)
                    r.raise_for_status()
                    agent_id = r.json()["id"]
                if step == "phone_clone":
                    clone_id = make_clone(api)
                    r, _, _ = api.request("PUT", f"/api/agents/{agent_id}", json={"voice_id": clone_id}, timeout=60)
                    if r.status_code != 200:
                        raise RuntimeError(f"could not set the cloned voice: HTTP {r.status_code} {err_text(r)}")
                    entry["note"] = f"PHONE_CLONE_VOICE in backend/.env = {res['phone_clone_voice_env']!r}"
                with Sampler(Api(a.api)) as smp:
                    if step == "tests":
                        entry.update(run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"], logs / "pytest.log", timeout=7200))
                        entry["pytest"] = parse_pytest(entry["tail"])
                        npm = shutil.which("npm.cmd" if IS_WIN else "npm") or "npm"
                        j = run([npm, "test", "--", "--ci", "--silent"], logs / "jest.log", cwd=REPO_ROOT / "frontend", timeout=3600)
                        entry["jest"] = parse_jest((REPO_ROOT / j["log"]).read_text(encoding="utf-8", errors="replace"))
                        entry["jest_wall_s"], entry["jest_exit"] = j["wall_s"], j["exit"]
                    else:
                        prefix, cmd = plans[step]
                        entry.update(run(cmd(), logs / f"{step}.log"))
                        f = newest(prefix, t0)
                        if f:
                            data = json.loads(f.read_text(encoding="utf-8"))
                            entry["result_file"] = f.name
                            entry["summary"] = ({"results": data.get("results"), "timings": data.get("timings")}
                                                if step == "retrieval" else data.get("summary"))
                        else:
                            entry["error"] = "no result file written"
                entry["peak"] = smp.peak
            except Exception as e:  # noqa: BLE001
                entry["error"] = f"{type(e).__name__}: {e}"
            entry.setdefault("wall_s", round(time.time() - t0, 1))
            res["steps"][step] = entry
            print(f"{step}: exit {entry.get('exit')}, {entry.get('wall_s')} s {entry.get('error', '')}", flush=True)
            for line in entry.get("tail", [])[-6:]:
                print("   ", line)
    finally:
        if clone_id:
            api.delete(f"/api/voices/{clone_id}", timeout=60)
        if agent_id:
            api.delete(f"/api/agents/{agent_id}", timeout=60)

    res["headline"] = headline(res["steps"])
    out = RESULTS_DIR / f"suite-{a.label}-{stamp}.json"
    out.write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    base = json.loads(Path(a.compare[0]).read_text(encoding="utf-8")) if a.compare else None
    md = markdown(base, res) if base else markdown(res)
    out.with_suffix(".md").write_text(md, encoding="utf-8")
    print("\n" + md)
    print(f"results: {out}\ntable:   {out.with_suffix('.md')}\nlogs:    {logs}")


if __name__ == "__main__":
    main()
