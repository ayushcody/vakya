"""First live contact with Gnani's Vachana API: the voice list, one Prisma transcription, one Timbre synthesis and
(optionally) one voice-clone embedding, printing the reply shapes so the guessed field names in services/gnani_client.py can be confirmed
or fixed in one go.

    backend/.venv/bin/python backend/bench/gnani_smoke.py [--clip path.wav] [--clone reference.wav] [--voice Nalini]

Reads GNANI_API_KEY from the environment or backend/.env. The key is never printed; error bodies are quoted truncated.
Outputs go to bench/results/gnani_smoke/ (voices.json, prisma.json, timbre.wav, embedding.json).

Probed on 3 Nov 2026 without a valid key: all four paths answer 401 INVALID_API_KEY with the X-API-Key-ID header (other
header names get MISSING_API_KEY), so the host, the paths and the header name are right; only the field names remain.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import wave
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

OUT = BACKEND / "bench" / "results" / "gnani_smoke"
DEFAULT_CLIP = BACKEND / "bench" / "results" / "stress_audio" / "clean" / "hi-01.wav"
TEXT = "नमस्ते, मैं वाक्या से बोल रही हूँ। आपका अपॉइंटमेंट कल सुबह दस बजे तय हो गया है।"


def load_env_key() -> bool:
    """Put GNANI_API_KEY in the environment from backend/.env if it is not there (never printed)."""
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


def shape(value, depth: int = 0):
    """A reply with every string/number replaced by its type and length, so the structure prints without the content."""
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [shape(value[0], depth + 1), f"... {len(value)} items"] if value else []
    if isinstance(value, str):
        return f"str[{len(value)}]"
    return type(value).__name__


def describe_error(e) -> str:
    details = getattr(e, "details", None) or {}
    return f"{getattr(e, 'code', type(e).__name__)}: {e}  details={json.dumps(details, ensure_ascii=False)[:400]}"


def step_voices() -> bool:
    from services import gnani_client
    print(f"\n== Voice list  GET {gnani_client.base_url()}/api/v1/tts/voices  (the path answers 401 without a key)")
    try:
        resp = gnani_client._request("GET", "/api/v1/tts/voices")
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        print("   FAILED:", describe_error(e))
        return False
    (OUT / "voices.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    items = data if isinstance(data, list) else next((v for v in data.values() if isinstance(v, list)), []) if isinstance(data, dict) else []
    print(f"   ok; {len(items)} entries; shape: {json.dumps(shape(data), ensure_ascii=False)[:400]}")
    for item in items[:60]:
        if isinstance(item, dict):
            print("   ", {k: item[k] for k in item if k in ("name", "voice", "voice_id", "id", "language", "language_code", "gender", "model")})
        else:
            print("   ", item)
    print(f"   Set GNANI_VOICES=Name:hi,Name:en,Name:hi-en from this list (saved to {OUT / 'voices.json'}).")
    return True


def step_prisma(clip: Path, language: str, keyterms: list[str]) -> bool:
    from services import gnani_client
    print(f"\n== Prisma STT  POST {gnani_client.base_url()}{gnani_client.STT_PATH}")
    print(f"   clip: {clip}  language_code={language}  keyterms={keyterms or 'none'}")
    audio = clip.read_bytes()
    t0 = time.perf_counter()
    try:
        data = gnani_client.transcribe(audio, language=language, keyterms=keyterms or None, filename=clip.name)
    except Exception as e:  # noqa: BLE001
        print("   FAILED:", describe_error(e))
        return False
    dt = time.perf_counter() - t0
    (OUT / "prisma.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   ok in {dt:.2f}s; reply keys: {sorted(k for k in data if k not in ('text', 'language', 'provider'))}")
    print(f"   text: {data['text']!r}")
    print(f"   detected language: {data.get('language')!r}")
    print(f"   shape: {json.dumps(shape(data), ensure_ascii=False)[:600]}")
    return True


def step_timbre(voice: str | None, language: str) -> bool:
    from services import gnani_client, gnani
    name = voice or gnani.voice_for(language)
    print(f"\n== Timbre TTS  POST {gnani_client.base_url()}{gnani_client.TTS_PATH}")
    print(f"   voice={name!r} language={language} sample_rate=24000 format=wav")
    t0 = time.perf_counter()
    try:
        audio = gnani_client.synthesize(TEXT, voice=name, language=language, sample_rate=24000, fmt="wav")
    except Exception as e:  # noqa: BLE001
        print("   FAILED:", describe_error(e))
        return False
    dt = time.perf_counter() - t0
    path = OUT / "timbre.wav"
    path.write_bytes(audio)
    info = ""
    try:
        with wave.open(str(path)) as w:
            info = f"{w.getnchannels()} ch, {w.getframerate()} Hz, {w.getnframes() / w.getframerate():.2f} s"
    except Exception:  # noqa: BLE001
        info = f"not a WAV container (first bytes {audio[:4]!r})"
    print(f"   ok in {dt:.2f}s; {len(audio)} bytes; {info}; saved {path}")
    # The phone path uses raw PCM16 at 8 kHz; check that too.
    try:
        raw = gnani_client.synthesize("नमस्ते", voice=name, language=language, sample_rate=8000, fmt="raw")
        print(f"   raw 8 kHz: {len(raw)} bytes = {len(raw) / 2 / 8000:.2f} s if PCM16")
    except Exception as e:  # noqa: BLE001
        print("   raw 8 kHz FAILED:", describe_error(e))
    return True


def step_embedding(reference: Path) -> bool:
    from services import gnani_client
    print(f"\n== Voice clone  POST {gnani_client.base_url()}{gnani_client.EMBEDDING_PATH}")
    print(f"   reference: {reference}")
    t0 = time.perf_counter()
    try:
        emb = gnani_client.clone_embedding(reference.read_bytes(), filename=reference.name)
    except Exception as e:  # noqa: BLE001
        print("   FAILED:", describe_error(e))
        return False
    dt = time.perf_counter() - t0
    (OUT / "embedding.json").write_text(json.dumps(emb), encoding="utf-8")
    print(f"   ok in {dt:.2f}s; {len(emb)} values (expected {gnani_client.EMBEDDING_DIMS})")
    try:
        audio = gnani_client.synthesize(TEXT, language="hi-IN", speaker_embedding=emb, sample_rate=24000, fmt="wav")
        (OUT / "timbre_clone.wav").write_bytes(audio)
        print(f"   cloned speech: {len(audio)} bytes saved to {OUT / 'timbre_clone.wav'}")
    except Exception as e:  # noqa: BLE001
        print("   cloned speech FAILED:", describe_error(e))
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--clip", default=str(DEFAULT_CLIP), help="a WAV of at most 30 s to transcribe")
    ap.add_argument("--language", default="hi-IN,en-IN", help="language_code for Prisma (or indic-auto)")
    ap.add_argument("--keyterms", default="", help="comma list of keyterms to send")
    ap.add_argument("--voice", default=None, help="Timbre voice name (default: GNANI_VOICES entry for the language)")
    ap.add_argument("--tts-language", default="hi-IN")
    ap.add_argument("--clone", default=None, help="a 5 to 30 s reference WAV to embed and speak with")
    ap.add_argument("--skip-tts", action="store_true")
    args = ap.parse_args()

    if not load_env_key():
        print("GNANI_API_KEY is not set (environment or backend/.env). Nothing was called.")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    clip = Path(args.clip)
    if not clip.is_file():
        print(f"clip not found: {clip} (run bench/stress_report.py --quick once to make the stress clips)")
        return 2
    results = {"voices": step_voices(), "prisma": step_prisma(clip, args.language, [t.strip() for t in args.keyterms.split(",") if t.strip()])}
    if not args.skip_tts:
        results["timbre"] = step_timbre(args.voice, args.tts_language)
    if args.clone:
        results["embedding"] = step_embedding(Path(args.clone))
    print("\n== Summary:", ", ".join(f"{k} {'ok' if v else 'FAILED'}" for k, v in results.items()))
    print("   Fix any FAILED step's field names in services/gnani_client.py (each guess is marked GUESS), then rerun.")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
