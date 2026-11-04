# Vakya: project status

_Last updated 4 November 2026. Submission deadline for Gnani AI's Great Indian AI Internship Challenge: 10 November 2026._

Vakya is a phone front desk for small businesses in India. It answers calls (Vobiz, 8 kHz) and browser web calls in Hindi, Marathi, Hinglish and English, checks the schedule for a free time, reads every number and date back and commits only on a spoken yes, remembers returning callers, and covers everything it says with a signed receipt, a C2PA content manifest and a file label. Speech is Gnani Prisma, Timbre and voice cloning; replies and document search are Gemini Flash and Gemini embeddings (keyword search as the backup); calls go through Vobiz. There is no local model and no second speech model. Everything below was tested on synthetic data only: no real caller, customer or patient recording has been used.

## What works, and how it was checked

| Area | Status | How it was checked |
|---|---|---|
| Front desks of eight trades, visual schedules, exact availability with nearest alternatives | works in tests | Availability engine tested directly; calendar, tester and agent tools share it (`docs/SCHEDULES.md`) |
| Booking with read-back, then commit on a spoken yes | works in tests | Dates and times resolved in code; **Caller Theatre: 9 of 9 scenarios passed, on a scripted model, text run** (`docs/THEATRE.md`). Not tried with Gemini Flash or a human on a phone |
| Proof of booking (sealed evidence per booking) | works in tests | Hashes and a signed receipt over the caller's words, the read-back and the yes |
| Caller memory across calls | works in tests | A profile per phone number, a returning-caller card on the Calls page, an opening line that uses it; fakes only (`docs/CALLER_MEMORY.md`) |
| Signed receipts, content manifest, file label, `/verify` page, offline SDK | works | Receipts verified by both SDKs in `sdk/verify/` (`docs/RECEIPT_SPEC.md`); revocation states tested (`docs/REVOCABLE_PROVENANCE.md`) |
| Consented voice cloning and voice vault | works in tests | Consent record, sealed files and withdrawal tested; the macOS Keychain path has not been used on a real login keychain (`docs/VOICE_VAULT.md`) |
| Browser web call link | works in tests | Backend and page tests with a fake microphone (`docs/WEB_CALL.md`) |
| Live monitor, takeover, call records, Watch it think split screen | tests only | Fake phone and mocked browser audio; never tried on a real call |
| Gnani gateway, usage ledger and cost meter | works in tests | One slot, cache, breaker, credit budget and held lines tested with fakes (`docs/GNANI.md`) |

## Integrated but not tested live

- **Gnani Prisma, Timbre and voice cloning.** Wired behind `GNANI_API_KEY` and tested against fakes. The API host and endpoints were confirmed to exist on 3 November (401 without a key); no valid key has been used, so request fields, voice names, latency and quotas are guesses, listed in `docs/GNANI.md`.
- **Gemini Flash and Gemini embeddings** with a live key on a real call.
- **Vobiz** calls, inbound calls, transfer, key presses and the hangup callback (`docs/PHONE.md`).
- **Missed-call callback:** a missed call to the number triggers a call back; built and tested with fakes, not yet tried on a live Vobiz number.
- **Google Calendar sync** for booked appointments: written against Google's API and tested with fakes; no real calendar has been used. A calendar failure never blocks a booking.
- **Hosted mode on Supabase:** tested against fakes only.

## Measured numbers

One thing is measured: **Caller Theatre, scripted model, text run, 9 of 9 scenarios passed, 41 of 41 booking fields correct.** It shows the tools and rules work end to end, not what a model does; its latency is not meaningful (scripted model) and is not reported. **Everything else is pending on Gnani:** the acoustic-stress report card on Prisma (the `/report-card` page is empty and says "Gnani Prisma run pending"), Prisma and Timbre latency, credits per call. See `docs/RESULTS.md`.

## Known issues

- **No second speech model.** When Gnani is busy or down, a live caller hears a short pre-rendered Timbre line and is offered a callback. This is deliberate; it is also the weakest point if the key or the credits run out mid-demo.
- **Gnani allows one active request at a time.** The gateway queues by priority (live call, desk, benchmark); a streaming recogniser would hold the slot, so the phone path stays on per-utterance requests until a live key shows otherwise.
- **Gemini free-tier limits** can run out in a long demo day.
- **Caller Theatre names:** booking names in Devanagari are refused by the name check, so a model must retry in Roman letters (`docs/THEATRE.md`, findings).
- **The tunnel needs the laptop awake** if the phone line is served from it (`docs/DEPLOY_DEMO.md`).
- **Receipt signing uses one key at a time**; rotation exists but the offline SDKs take one public key.
- **Local mode answers only on this computer**; another device needs `VAKYA_LOCAL_TOKEN`.

## What a person must do before submission

1. **Get a Gnani API key** (two registered team members, claim the credits) and put `GNANI_API_KEY` in `backend/.env`; put `GEMINI_API_KEY` beside it.
2. **Run `backend/bench/gnani_smoke.py`**, fix any `GUESS` it reports in `backend/services/gnani_client.py`, then run `./vakya stress --conditions all --provider gnani --keyterms` and fill `frontend/src/components/report/reportCardResults.json` (`docs/STRESS_REPORT.md`).
3. **Make one real call** through the Vobiz number, check that the booking lands in the calendar, then try the missed-call callback.
4. **Run Caller Theatre with audio and with Gemini Flash**, and update `docs/THEATRE.md` and `docs/RESULTS.md`.
5. **Record the 60-second video.** Every number on screen comes from a file in this repository, and any take that did not run on Gnani must say so.
6. **Make the repository public, post, and submit** by 10 November. Fill the links in `README.md`.

## Run it

```bash
./vakya setup
./vakya start
./vakya test    # backend and frontend tests
```

Settings are in `backend/.env` (see `backend/.env.example`). Docs: `docs/ARCHITECTURE.md`, `docs/GNANI.md`, `docs/PHONE.md`, `docs/WEB_CALL.md`, `docs/SCHEDULES.md`, `docs/THEATRE.md`, `docs/RESULTS.md`, `docs/STRESS_REPORT.md`, `docs/RECEIPT_SPEC.md`, `docs/REVOCABLE_PROVENANCE.md`, `docs/VOICE_VAULT.md`, `docs/CALLER_MEMORY.md`, `docs/SUBMISSION.md`, `docs/DEPLOY_DEMO.md`, `deploy/README.md`.
