# Vakya

A phone front desk for Indian small businesses (clinics, hotels, salons, restaurants, offices, institutes, service centres, gyms). It answers in Hindi, Marathi and English, checks a schedule for a free time, reads every detail back and books only after the caller says yes. Entry for Gnani AI's Great Indian AI Internship Challenge (deadline 10 Nov 2026, 11:59 PM IST).

## Stack (fixed; do not add other providers)

- Speech: Gnani Prisma (recognition), Gnani Timbre (speech), Gnani voice cloning. Every Gnani request goes through `backend/services/gnani_gateway.py` (one active request per account, priority lanes, cache, breaker, usage ledger). There is no second speech provider: when Gnani cannot answer, a live caller hears a pre-rendered Timbre hold line and is offered a callback.
- Language model and knowledge embeddings: Gemini through its OpenAI-compatible endpoint (`backend/services/gemini.py`, `services/llm.py`), keyword search as the backup for knowledge.
- Telephony: Vobiz only (`backend/services/telephony/vobiz.py`, `backend/routers/phone.py`).
- Never add self-hosted or downloadable models, other speech or model providers, or their names, to code, docs, UI or tests.

## Run

```bash
./vakya setup        # backend/.venv and frontend/node_modules
./vakya start        # API http://localhost:8020, web http://localhost:3010, phone-only port 8021
./vakya status
./vakya stop
./vakya doctor
./vakya demo         # invented clinic + hotel desks with staff bookings, so Bookings is never empty (adds only; backs up backend/data first)
```

Keys go in `backend/.env` (never commit it): `GNANI_API_KEY`, `GEMINI_API_KEY`, `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN`, `VOBIZ_CALLER_ID`, `PHONE_ALLOWED_NUMBERS`, `PUBLIC_BASE_URL` (a fixed HTTPS tunnel address for the phone line). `GET /api/ready` says what is missing without spending credits.

If the web app returns 500 on a font error after a restart: stop it, delete `frontend/.next`, start again (two dev servers on one `.next` corrupt it).

## Checks (run before every commit)

```bash
cd backend && .venv/bin/python -m pytest -q -p no:cacheprovider     # add --basetemp=/tmp/<dir> when two runs overlap
cd frontend && npx tsc --noEmit && npx jest && npx eslint .
```

## Map

- `backend/backend.py` app factory; `core/` state, deps, engines; `routers/` one file per feature; `services/` the logic.
- Front desks and templates: `backend/templates/agents/*.json|md` (eight trades, `remember` schema per trade).
- Schedules and availability: `services/schedules.py`, `availability.py`, `schedule_bookings.py`, `schedule_tools.py`, `hours_parser.py`; read-back rule `services/readback.py`; date and time reading `services/when.py`.
- Calls: `services/turn_pipeline.py`, `turn_stream.py`, `call_events.py` (Watch it think), `booking_evidence.py` (Proof of booking), `id_redact.py`, `grounding.py`.
- Caller memory: `services/caller_memory.py` (within a call), `call_notes.py` (long calls), `caller_profiles.py` (per number), `caller_ref.py`.
- Bench: `backend/bench/stress_report.py` (acoustic stress report card), `backend/bench/theatre/` (Caller Theatre).
- Frontend (Next.js): site in `frontend/src/app/(site)`, the desk in `frontend/src/app/(app)/desk`, shared list view `components/ui/ListView`, dates via `lib/formatDate.ts` ("Thu 12 Nov, 7 pm", Asia/Kolkata).
- Docs: `docs/ARCHITECTURE.md` first, then `GNANI.md`, `SCHEDULES.md`, `PHONE.md`, `CALLER_MEMORY.md`, `THEATRE.md`, `STRESS_REPORT.md`, `SUBMISSION.md`.

## Rules

- Synthetic data only: no real phone numbers, Aadhaar, PAN, card numbers or real business names in code, tests, fixtures or demos.
- Every claim on the site, README and submission must match what runs. No numbers that were not measured; label scripted runs as scripted.
- Design system: light, Gambarino display and Inter body, off-white `#fcf7f2`, black 8 px buttons, `#e6e6e6` borders, violet `#6c62ed` accent; logo files in `frontend/public/brand/`.
- Ports 8020, 3010 and 8021 belong to Vakya; do not change them.
- Keep `git` history clean: small commits with plain messages.
