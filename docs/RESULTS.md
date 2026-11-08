# Vakya measured results

Read this first. Three things are measured: live calls on 8 Nov 2026 (below), the acoustic-stress report card on **Gnani Prisma v3.0** (live API, 6 Nov 2026) and a **scripted** Caller Theatre run. Gnani voice cloning, the realtime Prisma stream, latency on a live phone turn and credits per call have not been measured, so no figure here says anything about them. Everything that has been measured is synthetic: no real caller has used Vakya and no real phone line carried these tests.

## Measured

### Caller Theatre, scripted run: 9 of 9 scenarios passed

Nine synthetic callers phone a front desk (an elderly Marathi caller, a rushed Hinglish caller, street noise, one wrong digit, a full slot, a cancellation, a returning caller, a request to note an ID number, a two-night hotel stay). The score comes from what ended up in the booking system. Run 20261104-052003: **9 of 9 passed, 41 of 41 booking fields correct.** Latency is not reported: with a scripted model the first reply takes a few milliseconds, which says nothing about a real model.

**This is a scripted run.** The model step was a deterministic stand-in, not the hosted language model, and the input was typed text: speech recognition, the caller voices and the line noise were not exercised (the audio run has not been made yet). What the pass shows is that the tool loop, the schedule store, the availability rules, the read-back and the confirm-before-cancel rule work end to end. It says nothing about what a language model does with the same calls, and any timing is the stand-in's, not a model's (shown as "scripted model, latency not meaningful"). One run per scenario. Method, scenarios and the findings the theatre made: `docs/THEATRE.md`.

### Live calls, 8 Nov 2026

Hand-run, not a measured rate. Real calls through Vobiz to an Indian mobile with Gnani Prisma and Timbre: booking A28 created, moved from 5 to 6 PM, and A27 cancelled, all in Hindi, each on the first yes. A Google Calendar event was created and deleted. WhatsApp approved templates (confirmed, moved, cancelled) in English, Hindi and Marathi were delivered. The caller was greeted by name with the upcoming appointment. **Reply start time was about 3.7 to 6 s, with first filler audio at about 1.1 s: too slow.** Inbound calls to our own number were not tested.

### Code tests

The backend and frontend test suites run against fakes of the outside services (`./vakya test`; 8 Nov: 3152 backend and 632 frontend tests pass). They show the code paths work, not that the services behave as the fakes assume.

## Acoustic-stress report card on Gnani Prisma (6 Nov 2026)

Prisma v3.0 (codemix), 24 invented booking calls (8 Hindi, 8 Marathi, 8 Hinglish) spoken by Gnani Timbre, task completion =
name, day and time all heard. Full tables and caveats: `docs/STRESS_REPORT.md`, `/report-card`.

| Condition | Plain | With keyterms |
| --- | ---: | ---: |
| clean, 8 kHz band, G.711, noise 10 dB, noise 5 dB | 100% | 100% |
| noise 0 dB | 96% | 100% |
| 8 kHz + noise 5 dB | 95% (n=21) | 100% |

Median recognition time 0.8 s per clip. Ten-digit phone numbers are the first field lost in noise (3 of 6 heard whole at 5 dB).
Synthetic speech and simulated noise: it shows the shape, not what a real caller will score.

## Pending: nothing is published until it has run

| What | Needs | Where it will land |
| --- | --- | --- |
| Prisma and Timbre latency on a phone turn, and credits per call and per booking | `GNANI_API_KEY` | the usage ledger (`GET /api/usage/summary`), `docs/GNANI.md` |
| Caller Theatre audio run (Timbre callers, Prisma recognition, line noise) | `GNANI_API_KEY` | `docs/THEATRE.md` |
| Caller Theatre with the hosted language model instead of the scripted stand-in | `LLM_API_KEY` | `docs/THEATRE.md` |
| Document search quality with hosted embeddings and with the keyword backup | `EMBEDDINGS_API_KEY` | here |
| A reply start time under 2 s on a real call (now 3.7 to 6 s) | latency work | `docs/PHONE.md` |
| The missed-call callback and inbound calls on a live Vobiz number | a Vobiz number | `docs/PHONE.md` |

## Not measured and not planned for this entry

- Any real caller, real accent or real phone network. All audio is synthetic.
- Real Marathi speakers' judgement of any Marathi output.
- Several callers at once.
