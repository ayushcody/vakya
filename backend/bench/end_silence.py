#!/usr/bin/env python3
"""End-of-speech wait: compare two values of PHONE_END_SILENCE_MS on recorded caller speech, without a call or a credit.

    backend/vakya/Scripts/python.exe backend/bench/end_silence.py [folder or .wav files ...] [--ms 400,500] [--json out.json]

Why. On a phone call the caller's turn ends after PHONE_END_SILENCE_MS of silence (default 500 ms; services/turn_taking.py
keeps longer waits for names, numbers and details). A shorter wait answers sooner, by exactly the difference, but cuts in on a
caller who pauses mid-sentence for longer than it, which slow and code-mixed speakers do. This replays each recording through
the phone router's own utterance detector (services/phone_audio.UtteranceDetector, the energy gate a live call uses) at each
value and counts, per recording, how many times the caller's turn would have been ended. A recording that is ONE turn at the
longer wait and more at the shorter one is a caller the shorter wait would have cut off.

Input: WAV files of what callers said, one utterance (or one stretch of caller speech) each: the caller side of recorded
calls, or the per-turn caller recordings Vakya stores (backend/data/audio, `turn_<conversation>*.wav`; a file per agent
reply is skipped when its name says so). Any rate and channel count; it is converted to the 8 kHz mono a phone call has.
With no recordings nothing is measured, and it says so: no number is made up.

Output: a table per value (turn ends, recordings cut off, the pauses that did it) and the time saved per turn, which is the
difference of the two values for every turn that uses the normal wait (turns asking for a detail keep their longer waits).
Pure replay: no network, no speech recognition, nothing written except --json."""
from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import phone_audio as pa  # noqa: E402

RATE = pa.PHONE_RATE
SKIP_WORDS = ("agent", "reply", "greeting", "hold", "_tts")


def read_wav(path: Path) -> bytes:
    """PCM16 mono at the phone rate."""
    with wave.open(str(path), "rb") as w:
        rate, ch, width, frames = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"{path.name}: only 16-bit WAV is read")
    x = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != RATE and len(x):
        n = int(round(len(x) * RATE / rate))
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return np.clip(x, -32768, 32767).astype("<i2").tobytes()


def turn_ends(pcm: bytes, end_ms: int, tail_ms: int = 1500) -> List[int]:
    """Where (ms into the audio) the phone detector would end the caller's turn with an end silence of `end_ms`. Silence is
    added after the recording so the last turn ends too. Only real utterances count (not "noise" blips)."""
    det = pa.UtteranceDetector(RATE, config=pa.DetectorConfig(end_silence_ms=end_ms))
    out: List[int] = []
    step = int(RATE * 0.02) * 2               # 20 ms of PCM16, as a call delivers it
    audio = pcm + b"\x00\x00" * int(RATE * tail_ms / 1000)
    for i in range(0, len(audio), step):
        for ev in det.feed(audio[i:i + step]):
            if ev.kind == "utterance":
                out.append(int(ev.at_ms))
    return out


def files_from(paths: Sequence[str]) -> List[Path]:
    found: List[Path] = []
    for p in paths:
        q = Path(p)
        if q.is_dir():
            found += sorted(f for f in q.rglob("*.wav") if not any(w in f.name.lower() for w in SKIP_WORDS))
        elif q.suffix.lower() == ".wav" and q.exists():
            found.append(q)
    return found


def compare(files: Sequence[Path], values: Sequence[int]) -> Dict[str, object]:
    """Per value: turn ends in all, and the recordings that end more often than at the longest value (cut off)."""
    longest = max(values)
    rows: List[Dict[str, object]] = []
    for f in files:
        try:
            pcm = read_wav(f)
        except Exception as e:  # noqa: BLE001 - a file that cannot be read is listed, not fatal
            rows.append({"file": f.name, "error": str(e)})
            continue
        rows.append({"file": f.name, "seconds": round(len(pcm) / 2 / RATE, 2),
                     "ends": {str(v): len(turn_ends(pcm, v)) for v in values}})
    good = [r for r in rows if "ends" in r]
    summary: Dict[str, object] = {}
    for v in values:
        cut = [r["file"] for r in good if r["ends"][str(v)] > r["ends"][str(longest)]]
        summary[str(v)] = {"turn_ends": sum(r["ends"][str(v)] for r in good), "cut_off": len(cut), "cut_off_files": cut,
                           "saved_ms_per_normal_turn": longest - v}
    return {"recordings": len(good), "unreadable": len(rows) - len(good), "values_ms": list(values), "longest_ms": longest,
            "per_value": summary, "rows": rows}


def report(result: Dict[str, object]) -> str:
    if not result["recordings"]:
        return ("No recordings to replay, so nothing was measured. Give it WAV files of callers speaking (the caller side of "
                "recorded calls, or backend/data/audio after a few real calls).")
    lines = [f"{result['recordings']} recordings, replayed through the phone detector (8 kHz, energy gate).",
             "", "end silence | turn ends | recordings cut off | saved per normal turn"]
    for v in result["values_ms"]:
        s = result["per_value"][str(v)]
        lines.append(f"{v:>8} ms | {s['turn_ends']:>9} | {s['cut_off']:>4} of {result['recordings']:<11} | "
                     f"{s['saved_ms_per_normal_turn']} ms")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", default=[str(BACKEND / "data" / "audio")])
    ap.add_argument("--ms", default="400,500", help="end-silence values to compare, e.g. 400,500")
    ap.add_argument("--json", help="also write the full result here")
    a = ap.parse_args(argv)
    values = sorted({int(x) for x in a.ms.split(",") if x.strip()})
    result = compare(files_from(a.paths), values)
    print(report(result))
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
