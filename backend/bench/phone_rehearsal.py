#!/usr/bin/env python3
"""Rehearse a phone call without a phone: the caller's words go in as text, the agent's replies come back as text.

It opens the real phone WebSocket of a running backend (routers/phone.py `vobiz_stream`) with a signed stream token that
carries the text flag (`make_token(..., text=True)`), and sends Vobiz's own `start` event with the caller's number, so the
call takes the same path as a real one: greeting choice (the known caller's personal opening or the agent's own), caller
profile and memory, the phone prompt, knowledge search, tools, the read-back rule, bookings, WhatsApp and Calendar side
effects, the stored conversation and the caller profile at hang-up. Only speech recognition and speech synthesis are
skipped (the caller's text becomes the turn, replies stay text). No Vobiz request is made and nobody is dialled.
The language model and embeddings are the real configured ones. Secrets and tokens are never printed.

    backend/.venv/bin/python backend/bench/phone_rehearsal.py --agent <id> --caller +91XXXXXXXXXX \\
        --say "clinic ke timings batao" --say "haan" --say "thank you, bye"
    backend/.venv/bin/python backend/bench/phone_rehearsal.py --agent <id> --caller +91... --script lines.txt --cancel-created

`--script` is a file with one caller line per row. After the call it lists what changed in the stores: new bookings
(id, date, time, calendar sync), WhatsApp outbox rows, the caller's profile. `--cancel-created` cancels the bookings the
call made through the staff API (that sends the cancellation WhatsApp, like staff cancelling would).
Results go to bench/results/phone_rehearsal-*.json.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

BENCH_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BENCH_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from common import Api, RESULTS_DIR, add_common_args, now_stamp, require_api  # noqa: E402
from phone_latency import agent_owner, _secret  # noqa: E402

DATA_DIR = BACKEND_DIR / "data"


def mint_text_token(agent_id: str, user_id: str, mode: str, ttl: int = 900, audio: bool = False) -> str:
    from routers import phone as px  # noqa: PLC0415
    secret = _secret()
    px._web_session = lambda: type("W", (), {"_secret": staticmethod(lambda: secret)})
    if audio:   # a real audio call that also reports each sentence and the end of each turn
        return px.make_token(agent_id, user_id, mode, ttl, report=True)
    return px.make_token(agent_id, user_id, mode, ttl, text=True)


def _json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def snapshot(api: Api, caller: str, agent_id: str) -> Dict[str, Any]:
    """Bookings (through the API), the WhatsApp outbox and the caller's profile, as they are now."""
    r = api.s.get(api.base + "/api/bookings", timeout=30)
    bookings = r.json().get("bookings", []) if r.ok else []
    outbox = _json(DATA_DIR / "whatsapp_outbox.json", {}).get("messages", [])
    profiles = _json(DATA_DIR / "caller_profiles.json", {})
    profile = next((v for v in profiles.values() if v.get("number") == caller), None)
    return {"bookings": bookings, "outbox": outbox, "profile": profile}


def wait_for_outbox(api: Api, caller: str, agent_id: str, seen: set, booking_ids: List[str], seconds: float) -> Dict[str, Any]:
    """Poll until each booking id has a WhatsApp row that is not pending (the send runs in the background), or `seconds` pass."""
    end = time.time() + seconds
    while True:
        snap = snapshot(api, caller, agent_id)
        rows = [m for m in snap["outbox"] if m["id"] not in seen]
        done = {m.get("booking_id") for m in rows if m.get("state") not in (None, "queued", "pending")}
        if all(b in done for b in booking_ids) or time.time() >= end:
            return snap
        time.sleep(1.0)


def mask(number: str) -> str:
    return "••••" + number[-4:]


async def run_call(url: str, caller: str, lines: List[str], timeout: float, pause: float) -> Dict[str, Any]:
    import websockets  # noqa: PLC0415
    out: Dict[str, Any] = {"greeting": None, "turns": [], "closed_by_agent": False, "error": None}
    async with websockets.connect(url, max_size=None, open_timeout=10) as ws:
        await ws.send(json.dumps({"event": "start", "sequenceNumber": 0,
                                  "start": {"callId": f"rehearsal-{int(time.time())}", "streamId": "MZrehearsal0001",
                                            "accountId": "rehearsal", "from": caller, "tracks": ["inbound"],
                                            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000}}}))

        async def frame() -> Optional[dict]:
            while True:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                except asyncio.TimeoutError:
                    out["error"] = "timeout waiting for the agent"
                    return None
                except Exception:  # noqa: BLE001 - the agent ended the call
                    out["closed_by_agent"] = True
                    return None
                if m.get("event") == "rehearsal":
                    return m

        g = await frame()
        out["greeting"] = g
        for line in lines:
            if g is None:
                break
            await asyncio.sleep(pause)
            await ws.send(json.dumps({"event": "rehearsal_text", "text": line}))
            turn: Dict[str, Any] = {"caller": line, "sentences": []}
            while True:
                m = await frame()
                if m is None:
                    break
                if m["type"] == "sentence":
                    turn["sentences"].append({"text": m["text"], "ms": m["ms"]})
                elif m["type"] == "done":
                    turn.update(m)
                    break
            out["turns"].append(turn)
            if m is None:
                break
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
    return out


# ------------------------------------------------------------------------------------------------ audio mode

AUDIO_DIR = BENCH_DIR / "data" / "rehearsal_audio"
RATE = 8000
CHUNK_S = 0.1
STREAM_SID = "MZrehearsal0001"
DEFAULT_SCRIPT = ["क्लिनिक के timings क्या हैं?", "मुझे बुधवार शाम पाँच बजे का appointment चाहिए", "हाँ", "धन्यवाद, बाय"]
API_LOG = BACKEND_DIR.parent / ".vakya" / "logs" / "api.log"


def caller_clip(api: Api, line: str, language: str = "hi") -> Dict[str, Any]:
    """The caller's line as 8 kHz PCM16 (spoken by a preset Timbre voice, kept on disk by the hash of the words so a
    repeat run spends no Gnani request) and where its speech ends, in seconds."""
    import hashlib  # noqa: PLC0415
    import io  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    import soundfile as sf  # noqa: PLC0415
    from common import tts_wav  # noqa: PLC0415
    from services import phone_audio as pa  # noqa: PLC0415
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIO_DIR / (hashlib.sha1(f"{language}|{line}".encode("utf-8")).hexdigest()[:16] + ".wav")
    if not path.exists():
        data, _secs = tts_wav(api, language, line)
        path.write_bytes(data)
    x, sr = sf.read(io.BytesIO(path.read_bytes()), dtype="float32", always_2d=False)
    x = np.asarray(x, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    x8 = pa.resample(x, int(sr), RATE)
    frame = int(RATE * 0.02)
    loud = [i for i in range(0, max(1, len(x8) - frame), frame) if float(np.sqrt(np.mean(x8[i:i + frame] ** 2))) > 0.01]
    end = (loud[-1] + frame) / RATE if loud else len(x8) / RATE
    return {"pcm": pa.float_to_pcm16(x8), "speech_end_s": end, "seconds": len(x8) / RATE, "line": line}


def _media(pcm: bytes, seq: int) -> str:
    """One Vobiz `media` frame: the caller's voice as base64 mu-law (8 kHz, like the phone network)."""
    import base64  # noqa: PLC0415
    from services import phone_audio as pa  # noqa: PLC0415
    return json.dumps({"event": "media", "sequenceNumber": seq, "streamId": STREAM_SID,
                       "media": {"track": "inbound", "chunk": seq, "timestamp": str(seq * 100),
                                 "payload": base64.b64encode(pa.ulaw_encode(pcm)).decode("ascii")}})


async def run_audio_call(url: str, caller: str, clips: List[Dict[str, Any]], timeout: float, pause: float) -> Dict[str, Any]:
    """A real call over the phone WebSocket: the caller's lines are streamed as mu-law media frames in real time with
    silence in between (a phone line is never silent), and each turn is timed from the end of the caller's speech."""
    import base64  # noqa: PLC0415
    import websockets  # noqa: PLC0415
    out: Dict[str, Any] = {"greeting": None, "turns": [], "closed_by_agent": False, "error": None, "mode": "audio"}
    step = int(RATE * 2 * CHUNK_S)
    silence = b"\x00\x00" * int(RATE * CHUNK_S)
    async with websockets.connect(url, max_size=None, open_timeout=10) as ws:
        t_start = time.perf_counter()
        await ws.send(json.dumps({"event": "start", "sequenceNumber": 0,
                                  "start": {"callId": f"rehearsal-{int(time.time())}", "streamId": STREAM_SID,
                                            "accountId": "rehearsal", "from": caller, "tracks": ["inbound"],
                                            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": RATE}}}))
        state: Dict[str, Any] = {"seq": 1, "base": 1, "audio_s": 0.0, "first_audio": None, "events": [], "ends": 0,
                                 "closed": False}

        async def listen() -> None:
            while True:
                try:
                    m = json.loads(await ws.recv())
                except Exception:  # noqa: BLE001 - closed
                    state["closed"] = True
                    return
                now = time.perf_counter()
                ev = m.get("event")
                if ev == "playAudio":
                    if state["first_audio"] is None:
                        state["first_audio"] = now
                    state["audio_s"] += len(base64.b64decode(m["media"]["payload"])) / RATE
                elif ev == "rehearsal":
                    state["events"].append((now, m))
                elif ev == "checkpoint" and str(m.get("name", "")).endswith("-end"):
                    state["ends"] += 1

        async def pump(pcm: Optional[bytes], t0: float, until) -> None:
            """Send `pcm` then silence in real time (one 100 ms frame each 100 ms) until `until()` or the timeout."""
            i = 0
            state["base"] = state["seq"]
            deadline = t0 + timeout + (len(pcm) / (RATE * 2) if pcm else 0)
            while not until() and time.perf_counter() < deadline and not state["closed"]:
                chunk = pcm[i:i + step] if (pcm is not None and i < len(pcm)) else silence
                i += step
                await asyncio.sleep(max(0.0, t0 + (state["seq"] - state["base"]) * CHUNK_S - time.perf_counter()))
                await ws.send(_media(chunk.ljust(step, b"\x00"), state["seq"]))
                state["seq"] += 1

        listener = asyncio.create_task(listen())
        await pump(None, time.perf_counter(), lambda: state["ends"] >= 1)    # the greeting arrives and ends
        if state["first_audio"] is None:
            out["error"] = "no greeting audio"
            listener.cancel()
            return out
        out["greeting"] = {"ms": int((state["first_audio"] - t_start) * 1000), "audio_s": round(state["audio_s"], 1)}
        play_end = state["first_audio"] + state["audio_s"]
        for clip in clips:
            if state["closed"]:
                out["closed_by_agent"] = True
                break
            wait_until = max(time.perf_counter(), play_end) + pause     # the caller listens to the end, then pauses
            await pump(None, time.perf_counter(), lambda: time.perf_counter() >= wait_until)
            state["first_audio"], state["audio_s"], state["events"] = None, 0.0, []
            ends_before = state["ends"]
            t0 = time.perf_counter()
            t_eos = t0 + clip["speech_end_s"]
            await pump(clip["pcm"], t0, lambda: state["ends"] > ends_before)
            turn: Dict[str, Any] = {"caller": clip["line"], "sentences": [], "first_audio_ms": None}
            if state["first_audio"] is not None:
                turn["first_audio_ms"] = int((state["first_audio"] - t_eos) * 1000)
            for at, m in state["events"]:
                if m.get("type") == "sentence":
                    turn["sentences"].append({"text": m["text"], "ms": int((at - t_eos) * 1000),
                                              "filler": bool(m.get("filler"))})
                elif m.get("type") == "done":
                    turn["done"] = {k: m.get(k) for k in ("text", "heard", "language", "timings", "tools")}
            real = [x for x in turn["sentences"] if not x["filler"]]
            turn["first_real_ms"] = real[0]["ms"] if real else None
            turn["filler"] = any(x["filler"] for x in turn["sentences"])
            if state["ends"] <= ends_before:
                turn["error"] = "no end of turn within the timeout"
            out["turns"].append(turn)
            play_end = (state["first_audio"] or time.perf_counter()) + state["audio_s"]
        listener.cancel()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
    return out


def show_audio(call: Dict[str, Any], log_lines: List[str]) -> None:
    import re  # noqa: PLC0415
    g = call["greeting"]
    if g:
        print(f"\n[greeting] start -> first audio {g['ms']} ms, {g['audio_s']} s long")
    heard: Dict[int, Any] = {}
    for ln in log_lines:
        m = re.search(r"phone turn (\d+) latency.*?heard=(-?\d+).*?speculative start: (\w+)", ln)
        if m:
            heard[int(m.group(1))] = (int(m.group(2)), m.group(3))
    print("\n turn | heard (ms after end of speech) | first audio | first real words | filler | caller -> agent")
    for i, t in enumerate(call["turns"], 2):
        h = heard.get(i)
        print(f" {i:>4} | {('%d' % h[0]) if h else '?':>8}{' (spec)' if h and h[1] == 'yes' else '':8} | "
              f"{t.get('first_audio_ms')!s:>8} ms | {t.get('first_real_ms')!s:>8} ms | {'YES' if t['filler'] else 'no':>6} | "
              f"{t['caller']} -> {(t.get('done') or {}).get('text') or '(none)'}{'  [' + t['error'] + ']' if t.get('error') else ''}")
        for s in t["sentences"]:
            print(f"        {s['ms']:>6} ms {'[filler] ' if s['filler'] else ''}{s['text']}")
        for c in (t.get("done") or {}).get("tools") or []:
            print(f"        tool {c['name']} {c['status']} {c['ms']} ms")
    for ln in log_lines:
        if "phone turn" in ln and ("latency" in ln or "pipeline" in ln or "greeting" in ln):
            print("  " + ln.strip()[:360])
        elif "greeting" in ln and "phone call" in ln:
            print("  " + ln.strip()[:360])
    if call["error"]:
        print("\nERROR:", call["error"])


def show(call: Dict[str, Any]) -> None:
    g = call["greeting"]
    if g:
        print(f"\n[greeting] ({g['ms']} ms, {'personal' if g.get('personal') else 'agent default'}, {g['language']})\n  AGENT: {g['text']}")
    for i, t in enumerate(call["turns"], 2):
        print(f"\n[turn {i}] CALLER: {t['caller']}")
        print(f"  AGENT: {t.get('text') or '(no reply)'}")
        if t.get("tools"):
            for c in t["tools"]:
                print(f"  tool: {c['name']} ok={c['ok']} status={c['status']} {c['ms']} ms args={json.dumps(c['args'], ensure_ascii=False)}")
        print(f"  first reply {t.get('first_reply_ms')} ms, total {t.get('total_ms')} ms, language {t.get('language')}, "
              f"pipeline {json.dumps(t.get('timings') or {})}")
    if call["error"]:
        print("\nERROR:", call["error"])
    if call["closed_by_agent"]:
        print("\n(the agent ended the call)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    add_common_args(p)
    p.add_argument("--agent", required=True)
    p.add_argument("--caller", required=True, help="the caller's number, E.164 (the caller ID of a real call)")
    p.add_argument("--mode", default="local", choices=("local", "cloud"))
    p.add_argument("--user-id", default=None)
    p.add_argument("--say", action="append", default=[], help="a caller line (repeat for each turn)")
    p.add_argument("--script", default=None, help="a text file, one caller line per row")
    p.add_argument("--audio", action="store_true",
                   help="speak the lines for real: Timbre-made speech, 8 kHz mu-law, streamed as media frames in real time "
                        "(recognition, end of speech, speculative start and speech synthesis all run); default script when "
                        "no line is given")
    p.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for each reply")
    p.add_argument("--pause", type=float, default=1.0, help="seconds the caller waits before speaking (after a reply)")
    p.add_argument("--settle", type=float, default=4.0, help="seconds to wait after hang-up for the call-end jobs")
    p.add_argument("--whatsapp-wait", type=float, default=50.0,
                   help="seconds to wait for the WhatsApp message of a booking (it goes WHATSAPP_SEND_DELAY_S, 30, after hang-up)")
    p.add_argument("--cancel-created", action="store_true", help="cancel the bookings this call made, through the staff API")
    args = p.parse_args()

    lines = list(args.say)
    if args.script:
        lines += [x.strip() for x in Path(args.script).read_text(encoding="utf-8").splitlines() if x.strip()]
    if not lines and args.audio:
        lines = list(DEFAULT_SCRIPT)
    if not lines:
        sys.exit("Give at least one --say line or a --script file.")
    api = Api(args.api)
    require_api(api)
    user_id = args.user_id or (agent_owner(args.agent) if args.mode == "local" else None)
    if not user_id:
        sys.exit("Unknown agent (or give --user-id for a cloud agent).")
    before = snapshot(api, args.caller, args.agent)
    base = args.api.rstrip("/").replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    url = f"{base}/api/phone/stream/{mint_text_token(args.agent, user_id, args.mode, audio=args.audio)}"
    print(f"Rehearsal call: agent {args.agent}, caller {mask(args.caller)}, {len(lines)} caller lines "
          f"({'spoken audio over the phone stream' if args.audio else 'text only'}; no Vobiz, no dialling)")
    if args.audio:
        clips = [caller_clip(api, x) for x in lines]
        log_from = API_LOG.stat().st_size if API_LOG.exists() else 0
        call = asyncio.run(run_audio_call(url, args.caller, clips, args.timeout, args.pause))
        time.sleep(1.0)
        try:
            with open(API_LOG, "rb") as f:
                f.seek(log_from)
                log_lines = f.read().decode("utf-8", "replace").splitlines()
        except OSError:
            log_lines = []
        show_audio(call, log_lines)
    else:
        call = asyncio.run(run_call(url, args.caller, lines, args.timeout, args.pause))
        show(call)
    time.sleep(max(0.0, args.settle))
    after = snapshot(api, args.caller, args.agent)
    made = [b["id"] for b in after["bookings"] if b["id"] not in {x["id"] for x in before["bookings"]}]
    if made:
        after = wait_for_outbox(api, args.caller, args.agent, {m["id"] for m in before["outbox"]}, made, args.whatsapp_wait)

    known = {b["id"] for b in before["bookings"]}
    created = [b for b in after["bookings"] if b["id"] not in known]
    changed = [b for b in after["bookings"] if b["id"] in known and
               b != next(x for x in before["bookings"] if x["id"] == b["id"])]
    seen = {m["id"] for m in before["outbox"]}
    sent = [m for m in after["outbox"] if m["id"] not in seen]
    print("\n--- side effects ---")
    for b in created:
        print(f"new booking {b['id']}: {b.get('start')} status={b.get('status')} calendar_sync={b.get('calendar_sync')} "
              f"name={b.get('name')} source={b.get('source')}")
    for b in changed:
        print(f"changed booking {b['id']}: {b.get('start')} status={b.get('status')} calendar_sync={b.get('calendar_sync')}")
    if not created and not changed:
        print("no booking was created or changed")
    for m in sent:
        print(f"whatsapp {m['kind']} {m.get('booking_id')} -> {m['phone_masked']}: {m['state']}"
              f"{' (' + m['reason'] + ')' if m.get('reason') else ''}{' ERROR ' + str(m['error']) if m.get('error') else ''}")
    if not sent:
        print("no WhatsApp message queued")
    pb, pa_ = before["profile"], after["profile"]
    if pa_:
        print(f"caller profile: calls {pb.get('call_count') if pb else None} -> {pa_.get('call_count')}, "
              f"last_booking {pa_.get('last_booking')}, last_outcome {pa_.get('last_outcome')}, "
              f"name {pa_.get('display_name')}, summary: {pa_.get('summary')}")

    cancelled: List[dict] = []
    if args.cancel_created:
        for b in created:
            r = api.s.delete(f"{api.base}/api/bookings/{b['id']}", timeout=60)
            cancelled.append({"id": b["id"], "status": r.status_code, "body": (r.json() if r.ok else r.text[:200])})
            print(f"cancelled {b['id']} through the staff API: HTTP {r.status_code}")
        seen2 = seen | {m["id"] for m in sent}
        final = wait_for_outbox(api, args.caller, args.agent, seen2, [c["id"] for c in cancelled], args.whatsapp_wait)
        for m in (m for m in final["outbox"] if m["id"] not in seen2):
            print(f"whatsapp {m['kind']} {m.get('booking_id')} -> {m['phone_masked']}: {m['state']}"
                  f"{' (' + m['reason'] + ')' if m.get('reason') else ''}")
        for b in final["bookings"]:
            if b["id"] in {c["id"] for c in cancelled}:
                print(f"booking {b['id']} now: status={b.get('status')} calendar_sync={b.get('calendar_sync')}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"phone_rehearsal-{now_stamp()}.json"
    safe = {"agent": args.agent, "caller": mask(args.caller), "call": call,
            "created": [b["id"] for b in created], "cancelled": cancelled}
    path.write_text(json.dumps(safe, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nresult: {path}")


if __name__ == "__main__":
    main()
