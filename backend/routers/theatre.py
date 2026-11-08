"""Caller Theatre results (docs/THEATRE.md), read-only. The files are written by bench/theatre/runner.py; nothing here runs a scenario
or calls any model. The data is synthetic (scripted callers, invented names and numbers), so the routes need no sign-in: the public
/theatre page and the desk page both read them.

    GET /api/theatre/latest[?mode=scripted|live]   the newest run (of that model mode), every scenario with its turns and verdict;
                                                    {"run": null} when no run has been made yet
    GET /api/theatre/runs                           every run on disk, newest first: id, model mode, input (audio|text), time, summary
    GET /api/theatre/runs/{run_id}                  one run by its id (the timestamp in the file name)
    GET /api/theatre/audio/{path}                   a caller or agent clip of a run (results/audio/<run>/<scenario>/<file>)
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

try:
    from core.errors import AppError
except ImportError:  # pragma: no cover
    from backend.core.errors import AppError

router = APIRouter(prefix="/api/theatre", tags=["theatre"])

RESULTS_DIR = Path(__file__).resolve().parent.parent / "bench" / "theatre" / "results"
RUN_FILE = re.compile(r"^(\d{8}-\d{6})-(scripted|live)-(audio|text)\.json$")
AUDIO_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav"}


def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _runs() -> List[Dict[str, Any]]:
    out = []
    for p in sorted(RESULTS_DIR.glob("*.json"), reverse=True):
        m = RUN_FILE.match(p.name)
        if not m:
            continue
        data = _read(p)
        if data is None:
            continue
        out.append({"run_id": data.get("run_id") or m.group(1), "mode": m.group(2), "input": m.group(3),
                    "created_at": data.get("created_at"), "label": data.get("label"), "summary": data.get("summary"),
                    "file": p.name})
    return out


@router.get("/latest")
def latest(mode: Optional[str] = Query(None, pattern="^(scripted|live)$")):
    path = RESULTS_DIR / (f"latest-{mode}.json" if mode else "latest.json")
    data = _read(path)
    return {"run": data}


@router.get("/runs")
def runs():
    return {"runs": _runs()}


@router.get("/runs/{run_id}")
def run(run_id: str, mode: Optional[str] = Query(None, pattern="^(scripted|live)$"),
        input: Optional[str] = Query(None, pattern="^(audio|text)$")):
    if not re.fullmatch(r"\d{8}-\d{6}", run_id):
        raise AppError("NOT_FOUND", "Run not found", 404)
    for r in _runs():
        if r["run_id"] == run_id and (not mode or r["mode"] == mode) and (not input or r["input"] == input):
            return {"run": _read(RESULTS_DIR / r["file"])}
    raise AppError("NOT_FOUND", "Run not found", 404)


@router.get("/audio/{path:path}")
def audio(path: str):
    base = (RESULTS_DIR / "audio").resolve()
    try:
        target = (base / path).resolve()
        found = base in target.parents and target.suffix.lower() in AUDIO_TYPES and target.is_file()
    except (OSError, ValueError):    # a NUL byte or a name too long for the file system is a clip that is not there
        found = False
    if not found:
        raise AppError("NOT_FOUND", "Clip not found", 404)
    return FileResponse(target, media_type=AUDIO_TYPES[target.suffix.lower()], headers={"Cache-Control": "public, max-age=3600"})
