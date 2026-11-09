#!/usr/bin/env python3
"""Snapshot the desk's data for the read-only public demo (frontend/public/demo-desk.json).

Run with Vakya running locally (./vakya start):  python3 frontend/scripts/snapshot_demo_desk.py

Every GET the desk makes is read from the local API once and stored under the same key lib/readonlyDesk.ts looks up
(the path plus the query parameters that change the answer). Private details are taken out on the way: full phone
numbers, the tunnel address, web-call link tokens, recordings and secrets. Calls are limited to the most recent few.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

API = "http://127.0.0.1:8020"
OUT = Path(__file__).resolve().parents[1] / "public" / "demo-desk.json"
KEEP = ("agent_id", "caller_ref", "conversation_id")   # the query parameters that change an answer (lib/readonlyDesk.ts)
MAX_CALLS = 15

_PHONE = re.compile(r"(?<![\w•])\+?\d[\d\s-]{8,}\d(?!\w)")
_URL = re.compile(r"https?://[^\s\"']*(ngrok|localhost|127\.0\.0\.1|trycloudflare)[^\s\"']*")
_SECRET_KEYS = re.compile(r"(token|secret|key|password|auth|signature|public_base_url|tunnel|webhook)", re.I)
_AUDIO_KEYS = re.compile(r"(audio_url|recording|audio_path|clip_url|audio_storage_path)", re.I)


def key_of(path: str) -> str:
    u = urllib.parse.urlsplit(path)
    q = urllib.parse.parse_qsl(u.query)
    kept = sorted((k, v) for k, v in q if k in KEEP)
    return u.path + ("?" + urllib.parse.urlencode(kept) if kept else "")


def get(path: str):
    req = urllib.request.Request(API + path, headers={"storage-mode": "local"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"null")
    except Exception as e:  # noqa: BLE001
        print(f"  skip {path}: {e}", file=sys.stderr)
        return None


def mask_phone(m: re.Match) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    return "••••" + digits[-4:] if len(digits) >= 10 else m.group(0)


def scrub(v, key: str = ""):
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if _AUDIO_KEYS.search(k):
                out[k] = None
            elif _SECRET_KEYS.search(k) and isinstance(x, (str, int, float)) and not isinstance(x, bool) \
                    and not k.endswith(("_set", "_masked", "_configured")) and k not in ("key", "outcome_key"):
                out[k] = None if isinstance(x, str) else x
            else:
                out[k] = scrub(x, k)
        return out
    if isinstance(v, list):
        return [scrub(x, key) for x in v]
    if isinstance(v, str):
        v = _URL.sub("https://vakya.ayushchougula.in", v)
        return _PHONE.sub(mask_phone, v)
    return v


def main() -> None:
    snap: dict = {}

    def take(path: str, transform=None):
        data = get(path)
        if data is None:
            return None
        if transform:
            data = transform(data)
        snap[key_of(path)] = scrub(data)
        return data

    agents = take("/api/agents") or []
    agent_rows = agents.get("agents", agents) if isinstance(agents, dict) else agents
    ids = [a["id"] for a in agent_rows]
    today = date.today()
    lo, hi = (today - timedelta(days=60)).isoformat(), (today + timedelta(days=60)).isoformat()

    for p in ("/api/health", "/api/engines", "/api/consent/mode", "/api/privacy/attestation", "/api/agent-templates",
              "/api/schedules", "/api/voices", "/api/usage/summary", "/api/usage/calls", "/api/usage",
              "/api/whatsapp/status", "/api/phone/status", "/api/phone/numbers", "/api/theatre/latest",
              "/api/theatre/runs", "/api/withdrawals", "/api/callers?limit=200"):
        take(p)
    def no_test_noise(d):   # the busy / down count is from our own test runs, not from callers
        if isinstance(d, dict) and isinstance(d.get("totals"), dict):
            d["totals"]["degraded"] = None
        return d
    snap.pop("/api/usage/summary", None)
    take("/api/usage/summary", no_test_noise)
    take("/api/phone/live", lambda d: {**d, "calls": []} if isinstance(d, dict) else d)
    take("/api/phone/missed-calls?limit=100", lambda d: {**d, "calls": []} if isinstance(d, dict) and "calls" in d else d)
    take("/api/whatsapp/messages?limit=50", lambda d: (d[:12] if isinstance(d, list) else {**d, "messages": (d.get("messages") or [])[:12]}))
    take(f"/api/bookings?from={lo}&to={hi}")

    schedules = get("/api/schedules") or {}
    srows = schedules.get("schedules", schedules) if isinstance(schedules, dict) else schedules
    for s in srows:
        sid = urllib.parse.quote(s["id"])
        take(f"/api/schedules/{sid}")
        take(f"/api/schedules/{sid}/today")
        wk = today - timedelta(days=today.weekday())   # this week and the next three (the calendar asks a week at a time)
        take(f"/api/schedules/{sid}/slots?from={(wk - timedelta(days=7)).isoformat()}&to={(wk + timedelta(days=27)).isoformat()}")

    for aid in ids:
        q = urllib.parse.quote(aid)
        take(f"/api/agents/{q}")
        take(f"/api/agents/{q}/documents")
        take(f"/api/agents/{q}/example-questions")
        take(f"/api/hearing/{q}")
        take(f"/api/schedules?agent_id={q}")
        snap[f"/api/agents/{aid}/web-call-links"] = {"links": []}
        take(f"/api/calls?days=365&limit=1&agent_id={q}")

    def recent(d):
        rows = [c for c in d.get("calls", []) if not c.get("practice") and c.get("replies", 0) >= 3][:MAX_CALLS]
        return {**d, "calls": rows}
    calls = take("/api/calls?range=7d&limit=200&hide_empty=true", recent) or {}
    for aid in ids:
        take(f"/api/calls?range=7d&limit=200&hide_empty=true&agent_id={urllib.parse.quote(aid)}",
             lambda d, aid=aid: {**recent(d), "calls": [c for c in recent(d)["calls"] if c.get("agent_id") == aid]})
    for c in calls.get("calls", []):
        take(f"/api/calls/{urllib.parse.quote(c['id'])}")
        b = c.get("booking") or {}
        if b.get("id"):
            take(f"/api/bookings/{urllib.parse.quote(b['id'])}/evidence")

    snap["_meta"] = {"made": today.isoformat(), "note": "Read-only demo snapshot. Calls, bookings and edits are off."}
    text = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
    # Provider names never reach the public page (spelled backwards here so this file names none either).
    names = "|".join(w[::-1] for w in ("ianepo", "ciporhtna", "edualc", "inimeg"))
    text = re.sub(rf"(?:{names})-compatible", "hosted-language-model", text, flags=re.I)
    for pat in (r"8788996295", r"ngrok", r"\+91\d{10}", rf"(?i){names}"):
        if re.search(pat, text):
            sys.exit(f"private detail left in the snapshot: {pat}")
    OUT.write_text(text)
    print(f"wrote {OUT} ({len(text) // 1024} KB, {len(snap) - 1} responses)")


if __name__ == "__main__":
    main()
