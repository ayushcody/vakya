# Vakya backend

The Vakya API: a phone receptionist for clinics and small offices in India. A caller rings (or gives a missed call and Vakya
calls back); the agent speaks first in the caller's language (Hindi, Marathi, English, code-mixed), books an appointment,
reads every number and date back, commits only on a spoken yes, and leaves a signed voice receipt that anyone can check at
`/verify`. Returning callers are remembered.

Speech recognition, speech and voice cloning come from Gnani's Prisma and Timbre (`GNANI_API_KEY`, see `../docs/GNANI.md`).
The language model is hosted (`services/llm.py`; keys and options in `.env.example`); knowledge search uses hosted embeddings with a keyword (BM25) backup. Nothing runs on
the machine except the app itself, so it needs only a small Python environment.

## Layout

| Folder | What is in it |
| --- | --- |
| `backend.py` | the app and nothing else: the FastAPI instance, middleware, router includes and the start-up and shut-down hooks (`uvicorn backend:app`) |
| `routers/` | the HTTP routes, one file per feature: `system` (health, engines, hardware), `auth`, `voices` (clone, preview, consent record, retention, transcribe), `agents`, `documents`, `turns` (the spoken turn and its audio), `phone` (phone calls over Vobiz), `web_call` (the browser call link), `conversations` (calls), `bookings`, `callers` (caller profiles), `consent`, `verify` (receipts), `privacy`, `templates`, `warmup`, `system_resources` |
| `services/` | the logic behind the routes. The turn: `turn_pipeline` (one turn, step by step), `turn_stream` (the streaming turn and its filler clips), `agent_prompt`, `asr`, `speaking`, `synthesis`, `knowledge` (documents and search). Voices: `voice_store`, `voice_clone`. Agents and storage: `agent_store`, `storage`, `llm_limits`. Also `gnani*`, `llm`, `retrieval`, `tools` (MCP client), `provenance` (signed receipts), `caller_memory`, `caller_profiles`, `phone_greeting`, `telephony/` (Vobiz), `gcal`, `voice_vault`, `web_session` ... |
| `mcp_servers/` | the appointment-booking MCP server the agents use as a tool |
| `core/` | `state` (Supabase, data directories: read as `state.x`, never copied), `engines` (the engine registry and routing), `deps` (the signed-in user, storage mode, id checks), `ratelimit`, `runtime` (one object over all of them for the phone and web-call code and the tests), errors, retry, validation, logging, memory guard, offline mode |
| `templates/agents/` | the receptionist and other agent templates |
| `webcall/` | the page and script of the browser call link |
| `bench/` | measurement scripts; `stress_report.py` is the acoustic-stress report card (`./vakya stress`) |
| `supabase/` | optional hosted-mode schema |
| `tests/` | the test suite |

## Run

From the repository root: `./vakya setup` once, then `./vakya start` (`.\vakya.ps1` on Windows). The API listens on port
**8020** and the web app on **3010** (`VAKYA_API_PORT` and `VAKYA_WEB_PORT` change them). The phone-only public port that a
tunnel exposes is **8021** (`PHONE_PUBLIC_PORT`).

By hand, from `backend/`:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt -c constraints.txt
cp .env.example .env            # every setting is documented there
.venv/bin/python -m uvicorn backend:app --host 127.0.0.1 --port 8020
```

`ffmpeg` (a system package) is used to read uploaded audio that is not plain WAV. `./vakya doctor` checks the keys, the
Python version, the ports and ffmpeg, and prints the fix for each problem.

## Tests

```bash
cd backend && .venv/bin/python -m pytest -q
```

## Credits and licences

- Speech: [Gnani AI](https://gnani.ai) Prisma (recognition), Timbre (speech) and voice cloning, a hosted API.
- Agents: a hosted language model, hosted embeddings.
- Frameworks: [FastAPI](https://fastapi.tiangolo.com/), [Next.js](https://nextjs.org/).
