# Vakya: project status

_Last updated 8 November 2026. Submission deadline for Gnani AI's Great Indian AI Internship Challenge: 10 November 2026._

Vakya is a phone front desk for small businesses in India. It answers calls (Vobiz, 8 kHz) and browser web calls in Hindi, Marathi, Hinglish and English, checks the schedule, reads every number and date back and commits only on a spoken yes, remembers returning callers, and covers bookings with a signed receipt. Speech is Gnani Prisma and Timbre; replies come from a hosted language model; calls go through Vobiz. There is no local model and no second speech model. No real caller recording has been used.

## Verified live on 8 November 2026

- Real phone calls through Vobiz to an Indian mobile, with Gnani Prisma (speech to text) and Timbre (voice).
- Booking A28 created, moved from 5 to 6 PM, and A27 cancelled, all in Hindi, each on the first yes.
- Google Calendar event created and deleted with the booking.
- WhatsApp approved templates (confirmed, moved, cancelled) in English, Hindi and Marathi delivered.
- Caller memory: greets by name and upcoming appointment, tracks rescheduled and cancelled status.

## Test counts (`./vakya test`, 8 November)

- Backend: 3152 passed.
- Frontend: 632 passed (58 suites).
- Type check and lint clean. These run against fakes of the outside services.

## Measured numbers

- Noise report card on Gnani Prisma v3.0 (6 Nov), 24 invented calls: 100% name, day and time heard down to 5 dB noise; 96% at 0 dB (100% with keyterms); median 0.8 s per clip; ten-digit numbers 3 of 6 heard whole at 5 dB. Synthetic audio. See `docs/RESULTS.md`.
- Caller Theatre, scripted model, text run: 9 of 9 scenarios, 41 of 41 fields. Not a speech or language-model result.
- Reply start time on live calls: about 3.7 to 6 s, first filler audio about 1.1 s. Too slow; being worked on.

## Not done or not tested live

- Inbound calls to our own Vobiz number and the missed-call callback: built, not tested live.
- Gnani voice cloning and the realtime Prisma stream: tested against fakes only.
- Reply latency under 2 s.
- Caller Theatre with audio and with a real language model.
- Hosted mode on Supabase: fakes only.

## Safety rules in progress

Another change is adding: 3-minute confirmation expiry, 60-minute cancel notice, at most 3 active bookings per number, no double booking, and an audit log at `GET /api/bookings/audit`. Not confirmed here until it lands and its tests pass.

## Known issues

- No second speech model: if Gnani is busy or down, the caller hears a pre-rendered line and a callback offer.
- Gnani allows one active request at a time; the gateway queues by priority.
- The language model's rate limits can run out in a long demo day.
- The tunnel needs the laptop awake if the phone line is served from it (`docs/DEPLOY_DEMO.md`).
- Receipt signing uses one key at a time.

## Before submission (10 November)

1. Record the 60-second video; every number on screen comes from a file in this repository.
2. Test one inbound call and the missed-call callback.
3. Make the repository public, post, and fill the links in `README.md`.

## Run it

```bash
./vakya setup
./vakya start
./vakya test
```

Docs: `docs/PITCH.md`, `docs/ARCHITECTURE.md`, `docs/GNANI.md`, `docs/PHONE.md`, `docs/SCHEDULES.md`, `docs/CALLER_MEMORY.md`, `docs/WHATSAPP.md`, `docs/CALENDAR.md`, `docs/RESULTS.md`, `docs/STRESS_REPORT.md`, `docs/THEATRE.md`, `deploy/README.md`.
