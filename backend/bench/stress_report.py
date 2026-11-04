#!/usr/bin/env python3
"""Acoustic-stress report card: how a voice receptionist's ear degrades on telephone audio.

    backend/.venv/bin/python backend/bench/stress_report.py --conditions all [--quick] [--keyterms]

What it does (docs/STRESS_REPORT.md has the numbers of the last run and the limits):
  1. Reads bench/data/stress_tasks.json: 24 booking-style utterances (8 Hindi, 8 Marathi, 8 Hinglish), each with a name, a
     date, a time and sometimes an amount or a phone number, every entity with a list of accepted spellings.
  2. Speaks the clean set once with Gnani Timbre (the voice GNANI_VOICES lists for the task's language), cached under
     bench/results/stress_audio/clean/. THE CLEAN AUDIO IS SYNTHETIC SPEECH, NOT REAL CALLERS.
  3. Degrades each clip (fixed seed): 8 kHz telephone band, babble-and-street noise at 10 / 5 / 0 dB SNR, G.711 mu-law, AMR
     (when ffmpeg has the encoder) and the telephone band with 5 dB noise. The SNR really applied is measured and reported.
  4. Recognises every clip with Gnani Prisma (the model and format GNANI_STT_MODEL / GNANI_STT_FORMAT pick) and scores it
     with services/asr_scoring: entity hit rate (accepted
     spellings), task completion (name AND date AND time heard), folded WER (best over the accepted reference texts) and
     the median recognition latency. `--keyterms` runs every condition twice, without and with a vocabulary list pooled
     over the whole task set (what an agent's documents would give; it is not each clip's own answer).
  5. Writes bench/results/stress_report-<date>.json and prints a Markdown table plus the five worst transcripts per
     condition, verbatim.

Nothing here talks to the API server; it imports the backend's Gnani client directly, so no server has to run. It needs
GNANI_API_KEY (environment or backend/.env); every request runs on the gateway's "bench" lane and spends Gnani credits.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import warnings
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

TASKS_PATH = HERE / "data" / "stress_tasks.json"
RESULTS_DIR = HERE / "results"
AUDIO_DIR = RESULTS_DIR / "stress_audio"
SR = 16000            # what Prisma is fed
CLEAN_SR = 24000      # what Timbre speaks at
SEED = 20261103
TTS_LANG = {"hi": "hi", "mr": "mr", "hi-en": "hinglish"}      # task language -> the app language code Timbre is asked for
LANGS = ("hi", "mr", "hi-en")
LANG_NAMES = {"hi": "Hindi", "mr": "Marathi", "hi-en": "Hinglish", "all": "all"}
QUICK_PER_LANG = 6
WORST_N = 5
CONDITIONS = ("clean", "tel8k", "noise_10db", "noise_5db", "noise_0db", "g711", "amr", "tel8k+noise_5db")
NOISE_SNR = {"noise_10db": 10.0, "noise_5db": 5.0, "noise_0db": 0.0, "tel8k+noise_5db": 5.0}


# --------------------------------------------------------------------------------------------------- audio helpers

def power(x: np.ndarray) -> float:
    return float(np.mean(np.square(x, dtype=np.float64))) if len(x) else 0.0


def snr_db(signal: np.ndarray, noise: np.ndarray) -> float:
    ps, pn = power(signal), power(noise)
    if pn <= 0:
        return float("inf")
    return 10.0 * float(np.log10(max(ps, 1e-20) / pn))


def resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to:
        return x.astype(np.float32)
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(sr_from, sr_to)
    return resample_poly(x, sr_to // g, sr_from // g).astype(np.float32)


def read_wav(path: Path) -> Tuple[np.ndarray, int]:
    import soundfile as sf
    x, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x.astype(np.float32), int(sr)


def write_wav(path: Path, x: np.ndarray, sr: int = SR) -> None:
    import soundfile as sf
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(x, -1.0, 1.0), sr, subtype="PCM_16")


def peak_guard(*arrays: np.ndarray, limit: float = 0.98) -> Tuple[float, ...]:
    """The arrays scaled by one common gain so the first one's peak is at most `limit` (SNR between them is unchanged)."""
    peak = float(np.max(np.abs(arrays[0]))) if len(arrays[0]) else 0.0
    gain = limit / peak if peak > limit else 1.0
    return (gain,) + tuple((a * gain).astype(np.float32) for a in arrays)


# ------------------------------------------------------------------------------------------------- degradations

def bandpass_300_3400(x: np.ndarray, sr: int = SR) -> np.ndarray:
    from scipy.signal import butter, sosfiltfilt
    sos = butter(6, [300.0, 3400.0], btype="bandpass", fs=sr, output="sos")
    return sosfiltfilt(sos, x).astype(np.float32)


def deg_tel8k(x: np.ndarray, sr: int = SR) -> np.ndarray:
    """Telephone band: 300-3400 Hz, carried at 8 kHz, then back to the recogniser's 16 kHz."""
    return resample(resample(bandpass_300_3400(x, sr), sr, 8000), 8000, sr)


def _audioop():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            import audioop  # noqa: PLC0415  (removed in Python 3.13; the ffmpeg path below covers that)
            return audioop
        except ImportError:
            return None


def _ffmpeg(*args: str) -> None:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg {' '.join(args[:6])} failed: {proc.stderr.strip()[:300]}")


def g711_audioop(x8: np.ndarray) -> np.ndarray:
    """mu-law encode and decode of 8 kHz audio with audioop (one byte per sample on the wire)."""
    ao = _audioop()
    if ao is None:
        raise RuntimeError("audioop is not available")
    pcm = (np.clip(x8, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    back = ao.ulaw2lin(ao.lin2ulaw(pcm, 2), 2)
    return np.frombuffer(back, dtype="<i2").astype(np.float32) / 32768.0


def g711_ffmpeg(x8: np.ndarray) -> np.ndarray:
    """The same round trip through ffmpeg's pcm_mulaw (used when audioop is missing)."""
    with tempfile.TemporaryDirectory() as td:
        a, b = Path(td, "in.wav"), Path(td, "ulaw.wav")
        write_wav(a, x8, 8000)
        _ffmpeg("-i", str(a), "-acodec", "pcm_mulaw", "-ar", "8000", "-ac", "1", str(b))
        y, sr = read_wav(b)
    return resample(y, sr, 8000)


def deg_g711(x: np.ndarray, sr: int = SR) -> np.ndarray:
    """G.711 mu-law, the codec of the phone network: down to 8 kHz, mu-law round trip, back to 16 kHz."""
    x8 = resample(x, sr, 8000)
    y8 = g711_audioop(x8) if _audioop() is not None else g711_ffmpeg(x8)
    return resample(y8, 8000, sr)


def amr_available() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return "libopencore_amrnb" in out


def deg_amr(x: np.ndarray, sr: int = SR) -> np.ndarray:
    """AMR-NB at 12.2 kbit/s (the mobile network's narrowband codec) through ffmpeg's libopencore_amrnb."""
    with tempfile.TemporaryDirectory() as td:
        a, b, c = Path(td, "in.wav"), Path(td, "x.amr"), Path(td, "out.wav")
        write_wav(a, resample(x, sr, 8000), 8000)
        _ffmpeg("-i", str(a), "-ar", "8000", "-ac", "1", "-c:a", "libopencore_amrnb", "-b:a", "12.2k", str(b))
        _ffmpeg("-i", str(b), "-ar", str(sr), "-ac", "1", str(c))
        y, ysr = read_wav(c)
    return resample(y, ysr, sr)


def coloured_noise(n: int, rng: np.random.Generator, sr: int = SR) -> np.ndarray:
    """Street-like noise: pink (1/f) hiss plus a low rumble (1/f^2 below 400 Hz), unit power."""
    spec = rng.standard_normal(n // 2 + 1) + 1j * rng.standard_normal(n // 2 + 1)
    f = np.maximum(np.fft.rfftfreq(n, 1.0 / sr), 20.0)
    shape = 1.0 / np.sqrt(f) + (f < 400) * (400.0 / f) ** 1.0 * 0.05
    y = np.fft.irfft(spec * shape, n)
    return (y / np.sqrt(power(y))).astype(np.float32)


def babble(n: int, others: List[np.ndarray], rng: np.random.Generator, voices: int = 4) -> np.ndarray:
    """Babble: `voices` other utterances, each looped and shifted by a random offset, summed, unit power."""
    pool = [o for o in others if len(o) > 0]
    if not pool:
        return coloured_noise(n, rng)
    mix = np.zeros(n, dtype=np.float64)
    for k in range(voices):
        src = pool[int(rng.integers(len(pool)))]
        reps = n // len(src) + 2
        looped = np.tile(src, reps)
        off = int(rng.integers(len(src)))
        seg = looped[off:off + n].astype(np.float64)
        mix += seg / np.sqrt(max(power(seg), 1e-12))
    return (mix / np.sqrt(max(power(mix), 1e-12))).astype(np.float32)


def make_noise(n: int, others: List[np.ndarray], rng: np.random.Generator) -> np.ndarray:
    """Equal power of babble and coloured street noise."""
    mixed = np.sqrt(0.5) * babble(n, others, rng) + np.sqrt(0.5) * coloured_noise(n, rng)
    return (mixed / np.sqrt(power(mixed))).astype(np.float32)


def add_noise(x: np.ndarray, snr_target_db: float, others: List[np.ndarray], rng: np.random.Generator
              ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Dict[str, float]]:
    """x plus noise at `snr_target_db` (whole-clip power ratio). Returns (mixed, the clean signal, the noise, as they
    sit in `mixed` after a common peak-guard gain, info with the SNR measured on those two)."""
    noise = make_noise(len(x), others, rng)
    noise = noise * np.sqrt(power(x) / (10.0 ** (snr_target_db / 10.0)))
    gain, _, xs, ns = peak_guard(x + noise, x, noise)
    mixed = (xs + ns).astype(np.float32)
    return mixed, xs, ns, {"snr_db": snr_db(xs, ns), "gain": gain}


def stable_seed(*parts: Any) -> int:
    return (SEED + zlib.crc32("|".join(str(p) for p in parts).encode())) % (2 ** 32)


def degrade(condition: str, x: np.ndarray, item_id: str, others: List[np.ndarray], sr: int = SR
            ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """One condition applied to one 16 kHz clip. Returns (audio, info); info["snr_db"] is the SNR applied at the source
    (noise conditions), info["snr_post_channel_db"] the SNR after the telephone band for the combined one."""
    info: Dict[str, Any] = {}
    if condition == "clean":
        return x, info
    if condition == "tel8k":
        return deg_tel8k(x, sr), info
    if condition == "g711":
        return deg_g711(x, sr), info
    if condition == "amr":
        return deg_amr(x, sr), info
    if condition in NOISE_SNR:
        rng = np.random.default_rng(stable_seed(condition, item_id))
        mixed, xs, ns, ninfo = add_noise(x, NOISE_SNR[condition], others, rng)
        info.update(ninfo)
        if condition.startswith("tel8k+"):   # the channel is linear: filter both parts to know the SNR after it
            info["snr_post_channel_db"] = snr_db(deg_tel8k(xs, sr), deg_tel8k(ns, sr))
            mixed = deg_tel8k(mixed, sr)
        return mixed, info
    raise ValueError(f"unknown condition {condition!r}")


def condition_available(condition: str) -> Optional[str]:
    """None when the condition can run here, else the reason it is skipped."""
    if condition == "amr" and not amr_available():
        return "this ffmpeg build has no libopencore_amrnb encoder (brew's ffmpeg does not; a build with --enable-libopencore-amrnb does)"
    if condition == "g711" and _audioop() is None and not shutil.which("ffmpeg"):
        return "neither audioop nor ffmpeg is available"
    return None


# ------------------------------------------------------------------------------------------------------ the task set

def load_tasks(path: Path = TASKS_PATH, quick: bool = False) -> List[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tasks = data["tasks"] if isinstance(data, dict) else data
    if not quick:
        return tasks
    out = []
    for lang in LANGS:
        out += [t for t in tasks if t["language"] == lang][:QUICK_PER_LANG]
    return out


def keyterm_pool(tasks: List[dict]) -> List[str]:
    """The vocabulary an agent's documents would hold: the first two accepted spellings of every entity of every task
    (names, weekdays, amounts) except phone numbers, pooled so no clip's own answer is singled out."""
    seen, out = set(), []
    for t in tasks:
        for kind, spellings in t["entities"].items():
            if kind in ("phone", "time"):
                continue
            for s in spellings[:2]:
                if s.lower() not in seen:
                    seen.add(s.lower())
                    out.append(s)
    return out[:200]


# ------------------------------------------------------------------------------------------------------- synthesis

def synthesize_clean(tasks: List[dict], out_dir: Path = AUDIO_DIR / "clean") -> Dict[str, Path]:
    """Gnani Timbre (24 kHz WAV), one voice per task language, cached per item id."""
    paths: Dict[str, Path] = {}
    gnani = None
    for t in tasks:
        p = out_dir / f"{t['id']}.wav"
        paths[t["id"]] = p
        if p.exists() and p.stat().st_size > 1000:
            continue
        if gnani is None:   # lazy: the report card runs without a key when every clip is cached and no recogniser is built
            from services import gnani   # noqa: PLW0621
        text = t.get("tts_text") or t["text"]
        x, rate = gnani.synth(text, TTS_LANG.get(t["language"], t["language"]))
        if len(x) == 0:
            raise RuntimeError("Timbre produced no audio")
        write_wav(p, x, rate)
        print(f"  spoke {t['id']} (Timbre, {gnani.voice_for(TTS_LANG.get(t['language'], t['language']))})", flush=True)
    return paths


# ------------------------------------------------------------------------------------------------------ recognisers

GNANI_LANGS = {"hi": ["hi"], "mr": ["mr"], "hi-en": ["hinglish"]}


class Recognizer:
    """One provider behind one call: (wav path, task language, keyterms or None) -> (text, seconds)."""
    name = "?"
    model = "?"
    supports_keyterms = False

    def __call__(self, wav: Path, language: str, keyterms: Optional[List[str]] = None) -> Tuple[str, float]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class GnaniPrisma(Recognizer):
    name = "gnani"
    supports_keyterms = True

    def __init__(self):
        from services import gnani
        if not gnani.configured():
            raise RuntimeError("GNANI_API_KEY is not set")
        self.mod = gnani
        self.model = f"{gnani.stt_model()} ({gnani.stt_format()})"

    def __call__(self, wav, language, keyterms=None):
        data = Path(wav).read_bytes()
        t0 = time.perf_counter()
        out = self.mod.transcribe(data, filename=Path(wav).name, languages=GNANI_LANGS.get(language, [language]),
                                  keyterms=keyterms or None)
        return (out.get("text") or "").strip(), time.perf_counter() - t0


def load_env_key() -> bool:
    """Put GNANI_API_KEY in the environment from backend/.env when it is not there already (never printed)."""
    if os.getenv("GNANI_API_KEY", "").strip():
        return True
    env = BACKEND / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*(?:export\s+)?GNANI_API_KEY\s*=\s*(.+?)\s*$", line)
            if m and m.group(1).strip().strip("\"'"):
                os.environ["GNANI_API_KEY"] = m.group(1).strip().strip("\"'")
                return True
    return False


def make_recognizer() -> Recognizer:
    if not load_env_key():
        raise SystemExit("GNANI_API_KEY is not set (environment or backend/.env): nothing was called.")
    return GnaniPrisma()


# --------------------------------------------------------------------------------------------------------- scoring

def score_item(task: dict, hyp: str) -> Dict[str, Any]:
    from services import asr_scoring as sc
    ents = task["entities"]
    hits = sc.entity_hits(hyp, ents)
    refs = [task["text"]] + ([task["tts_text"]] if task.get("tts_text") else [])
    return {"entity_hits": hits, "entity_hit_rate": sc.entity_hit_rate(hyp, ents), "task_ok": sc.task_ok(hyp, ents),
            "wer_folded": sc.accepted_wer(refs, hyp, folded=True)}


def _median(values: List[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def summarise(records: List[dict]) -> List[dict]:
    """One row per (mode, condition, language), then one "all" row per (mode, condition)."""
    rows = []
    keys = sorted({(r["mode"], r["condition"]) for r in records}, key=lambda k: (k[0], CONDITIONS.index(k[1])))
    for mode, cond in keys:
        mine = [r for r in records if r["mode"] == mode and r["condition"] == cond and not r.get("error")]
        for lang in ("all",) + LANGS:
            sub = [r for r in mine if lang == "all" or r["language"] == lang]
            if not sub:
                continue
            kinds: Dict[str, List[bool]] = {}
            for r in sub:
                for k, v in r["entity_hits"].items():
                    kinds.setdefault(k, []).append(v)
            snrs = [r["snr_db"] for r in sub if r.get("snr_db") is not None]
            rows.append({
                "mode": mode, "condition": cond, "language": lang, "n": len(sub),
                "entity_hit_rate": float(np.mean([r["entity_hit_rate"] for r in sub])),
                "task_completion": float(np.mean([r["task_ok"] for r in sub])),
                "wer_folded": float(np.mean([r["wer_folded"] for r in sub])),
                "latency_median_s": _median([r["seconds"] for r in sub]),
                "hit_by_kind": {k: float(np.mean(v)) for k, v in sorted(kinds.items())},
                "snr_db": float(np.mean(snrs)) if snrs else None,
            })
    return rows


def pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{100 * v:.0f}%"


def markdown_table(rows: List[dict], mode: str) -> str:
    lines = ["| Condition | Language | n | Entity hit rate | Task completion | Folded WER | Median latency | SNR applied |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        if r["mode"] != mode:
            continue
        lat = "n/a" if r["latency_median_s"] is None else f"{r['latency_median_s']:.2f} s"
        snr = "-" if r["snr_db"] is None else f"{round(r['snr_db'], 1) + 0.0:.1f} dB"
        bold = "**" if r["language"] == "all" else ""
        lines.append(f"| {bold}{r['condition']}{bold} | {LANG_NAMES[r['language']]} | {r['n']} | {pct(r['entity_hit_rate'])} | "
                     f"{pct(r['task_completion'])} | {r['wer_folded']:.2f} | {lat} | {snr} |")
    return "\n".join(lines)


def failure_rank(r: dict) -> Tuple[int, float, float]:
    """Worst first: not completable, fewest entities heard, highest WER."""
    return (1 if r["task_ok"] else 0, r["entity_hit_rate"], -r["wer_folded"])


def failures_appendix(records: List[dict], mode: str, n: int = WORST_N) -> str:
    out = []
    for cond in CONDITIONS:
        mine = sorted([r for r in records if r["mode"] == mode and r["condition"] == cond and not r.get("error")],
                      key=failure_rank)[:n]
        if not mine:
            continue
        out.append(f"#### {cond}")
        for r in mine:
            missed = [k for k, v in r["entity_hits"].items() if not v] or ["none"]
            out.append(f"- `{r['id']}` ({LANG_NAMES[r['language']]}; task {'ok' if r['task_ok'] else 'NOT completable'}; "
                       f"missed: {', '.join(missed)}; folded WER {r['wer_folded']:.2f})")
            out.append(f"  - said: {r['text']}")
            out.append(f"  - heard: {r['hypothesis'] or '(nothing)'}")
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------------------------------------- the run

def run(tasks: List[dict], conditions: List[str], recognizer: Recognizer, keyterms_modes: List[Optional[List[str]]],
        audio_dir: Path = AUDIO_DIR, clean_paths: Optional[Dict[str, Path]] = None, log: Callable[[str], None] = print
        ) -> Tuple[List[dict], List[dict]]:
    """Degrades and recognises every (condition, task, keyterm mode). Returns (records, skipped conditions)."""
    clean16: Dict[str, np.ndarray] = {}
    for t in tasks:
        x, sr = read_wav(clean_paths[t["id"]])
        clean16[t["id"]] = resample(x, sr, SR)
    records: List[dict] = []
    skipped: List[dict] = []
    for cond in conditions:
        reason = condition_available(cond)
        if reason:
            skipped.append({"condition": cond, "reason": reason})
            log(f"skip {cond}: {reason}")
            continue
        wavs: Dict[str, Tuple[Path, Dict[str, Any]]] = {}
        for t in tasks:
            others = [clean16[o["id"]] for o in tasks if o["id"] != t["id"]]
            y, info = degrade(cond, clean16[t["id"]], t["id"], others)
            p = audio_dir / cond / f"{t['id']}.wav"
            write_wav(p, y, SR)
            wavs[t["id"]] = (p, info)
        snrs = [i["snr_db"] for _, i in wavs.values() if "snr_db" in i]
        log(f"{cond}: {len(tasks)} clips" + (f", SNR applied mean {round(float(np.mean(snrs)), 2) + 0.0:.2f} dB (min {round(min(snrs), 2) + 0.0:.2f}, max {round(max(snrs), 2) + 0.0:.2f})" if snrs else ""))
        for terms in keyterms_modes:
            mode = "keyterms" if terms else "plain"
            for t in tasks:
                p, info = wavs[t["id"]]
                rec = {"mode": mode, "condition": cond, "id": t["id"], "language": t["language"], "text": t["text"],
                       "snr_db": info.get("snr_db"), "snr_post_channel_db": info.get("snr_post_channel_db")}
                try:
                    hyp, secs = recognizer(p, t["language"], terms)
                    rec.update(hypothesis=hyp, seconds=secs, **score_item(t, hyp))
                except Exception as e:  # noqa: BLE001
                    rec.update(hypothesis="", seconds=0.0, error=f"{type(e).__name__}: {e}"[:300], entity_hits={},
                               entity_hit_rate=0.0, task_ok=False, wer_folded=1.0)
                    log(f"  {cond} {t['id']} failed: {rec['error']}")
                records.append(rec)
    return records, skipped


def host_info() -> dict:
    info = {"platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version()}
    with contextlib.suppress(Exception):
        info["chip"] = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    with contextlib.suppress(Exception):
        info["commit"] = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                        cwd=BACKEND).stdout.strip()
    return info


def parse_conditions(spec: str) -> List[str]:
    if spec.strip().lower() == "all":
        return list(CONDITIONS)
    names = [c.strip() for c in spec.split(",") if c.strip()]
    bad = [c for c in names if c not in CONDITIONS]
    if bad:
        raise SystemExit(f"unknown condition(s) {bad}; choose from {', '.join(CONDITIONS)} or 'all'")
    return names


def build_report(records, skipped, meta) -> dict:
    return {"meta": meta, "summary": summarise(records), "skipped": skipped, "records": records}


REPORT_CARD_PATH = BACKEND.parent / "frontend" / "src" / "components" / "report" / "reportCardResults.json"


def report_card(report: dict) -> dict:
    """The smaller file the web pages read (docs/STRESS_REPORT.md, "The results file"): one row per (condition, language,
    keyterms), task completion and the hit rate per kind in percent, the folded WER as a fraction, the median seconds."""
    meta = report["meta"]
    rows = []
    for r in report["summary"]:
        rows.append({
            "condition": r["condition"], "language": r["language"], "keyterms": r["mode"] == "keyterms", "n": r["n"],
            "taskCompletion": round(100 * r["task_completion"], 1), "wer": round(r["wer_folded"], 3),
            "latencyS": round(r["latency_median_s"], 2) if r.get("latency_median_s") is not None else 0.0,
            "hitByKind": {k: round(100 * v, 1) for k, v in r["hit_by_kind"].items()},
        })
    return {"run": {"date": meta["date"], "model": meta["model"], "tasks": meta["tasks"],
                    "commit": (meta.get("host") or {}).get("commit") or "", "rows": rows}}


def gateway_scope():
    """Every Gnani request of a run is a "bench" request on the gateway (lowest priority, never starves a live call)."""
    try:
        from services import gnani_gateway
    except ImportError:  # pragma: no cover
        from backend.services import gnani_gateway
    return gnani_gateway.scope("bench")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--conditions", default="all", help=f"'all' or a comma list of: {', '.join(CONDITIONS)}")
    ap.add_argument("--quick", action="store_true", help=f"{QUICK_PER_LANG} items per language")
    ap.add_argument("--keyterms", action="store_true", help="run each condition without and with the pooled vocabulary")
    ap.add_argument("--tasks", default=str(TASKS_PATH))
    ap.add_argument("--out", default=None, help="result JSON (default: results/stress_report-<date>.json)")
    ap.add_argument("--write-report-card", nargs="?", const=str(REPORT_CARD_PATH), default=None, metavar="PATH",
                    help="also write the summary in the shape the web pages read (default: "
                         "frontend/src/components/report/reportCardResults.json)")
    args = ap.parse_args(argv)

    conditions = parse_conditions(args.conditions)
    tasks = load_tasks(Path(args.tasks), args.quick)
    provider = "gnani"
    print(f"provider: {provider}; {len(tasks)} tasks; conditions: {', '.join(conditions)}", flush=True)
    recognizer = make_recognizer()
    try:
        print("speaking the clean set with Gnani Timbre", flush=True)
        with gateway_scope():
            clean_paths = synthesize_clean(tasks)
        pool = keyterm_pool(load_tasks(Path(args.tasks), False))
        modes: List[Optional[List[str]]] = [None]
        notes = []
        if args.keyterms:
            modes.append(pool)
        with gateway_scope():
            records, skipped = run(tasks, conditions, recognizer, modes, clean_paths=clean_paths)
        meta = {"date": dt.date.today().isoformat(), "created": dt.datetime.now().isoformat(timespec="seconds"),
                "provider": provider, "model": recognizer.model, "quick": args.quick, "tasks": len(tasks), "conditions": conditions, "seed": SEED, "tts_voice": "Gnani Timbre",
                "clean_audio": "synthetic Timbre speech, not real callers", "keyterms": len(pool) if args.keyterms else 0,
                "notes": notes, "host": host_info()}
    finally:
        recognizer.close()
    report = build_report(records, skipped, meta)
    out = Path(args.out) if args.out else RESULTS_DIR / f"stress_report-{meta['date']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n## Acoustic-stress report ({meta['date']}, provider {provider}, model {meta['model']}"
          f"{', quick' if args.quick else ''})\n")
    for mode in ("plain", "keyterms") if args.keyterms else ("plain",):
        print(f"### {'Without' if mode == 'plain' else 'With'} keyterms\n")
        print(markdown_table(report["summary"], mode) + "\n")
    for s in skipped:
        print(f"- skipped `{s['condition']}`: {s['reason']}")
    for n in meta["notes"]:
        print(f"- note: {n}")
    print("\n### Failures: the worst five transcripts per condition (plain run)\n")
    print(failures_appendix(records, "plain"))
    print(f"result file: {out}")
    if args.write_report_card:
        card = Path(args.write_report_card)
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_text(json.dumps(report_card(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"report card file: {card}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
