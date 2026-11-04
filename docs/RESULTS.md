# Vakya measured results

Read this first. Almost nothing about Vakya has been measured on its current stack, and this page says so rather than carrying numbers over from an earlier one. **Gnani Prisma, Timbre and cloning have not been run against the live API**, so no accuracy, latency, noise or cost figure here says anything about them. Everything that has been measured is synthetic: no real caller has used Vakya and no real phone line carried these tests.

## Measured

### Caller Theatre, scripted run: 9 of 9 scenarios passed

Nine synthetic callers phone a front desk (an elderly Marathi caller, a rushed Hinglish caller, street noise, one wrong digit, a full slot, a cancellation, a returning caller, a request to note an ID number, a two-night hotel stay). The score comes from what ended up in the booking system. Run 20261104-052003: **9 of 9 passed, 41 of 41 booking fields correct.** Latency is not reported: with a scripted model the first reply takes a few milliseconds, which says nothing about a real model.

**This is a scripted run.** The model step was a deterministic stand-in, not Embeddings Flash, and the input was typed text: speech recognition, the caller voices and the line noise were not exercised (the audio run is pending the Gnani key). What the pass shows is that the tool loop, the schedule store, the availability rules, the read-back and the confirm-before-cancel rule work end to end. It says nothing about what a language model does with the same calls, and any timing is the stand-in's, not a model's (shown as "scripted model, latency not meaningful"). One run per scenario. Method, scenarios and the findings the theatre made: `docs/THEATRE.md`.

### Code tests

The backend and frontend test suites run against fakes of Gnani, Embeddings and Vobiz (`./vakya test`). They show the code paths work, not that the services behave as the fakes assume.

## Pending: nothing is published until it has run

| What | Needs | Where it will land |
| --- | --- | --- |
| Gnani Prisma on the acoustic-stress task set: booking completion under an 8 kHz line, street noise and G.711, with and without keyterms; which field is lost | `GNANI_API_KEY` | `/report-card`, `docs/STRESS_REPORT.md` |
| Prisma and Timbre latency on a phone turn, and credits per call and per booking | `GNANI_API_KEY` | the usage ledger (`GET /api/usage/summary`), `docs/GNANI.md` |
| Caller Theatre audio run (Timbre callers, Prisma recognition, line noise) | `GNANI_API_KEY` | `docs/THEATRE.md` |
| Caller Theatre with Embeddings Flash instead of the scripted stand-in | `EMBEDDINGS_API_KEY` | `docs/THEATRE.md` |
| Document search quality with Embeddings embeddings and with the keyword backup | `EMBEDDINGS_API_KEY` | here |
| The spoken AI disclosure opening every call | a live call | here |
| First-reply latency on a real Vobiz call | a Vobiz number and the keys | `docs/PHONE.md` |
| The missed-call callback on a live Vobiz number, and Google Calendar sync against a real calendar | a Vobiz number, a calendar | `docs/PHONE.md` |

## Not measured and not planned for this entry

- Any real caller, real accent or real phone network. All audio is synthetic.
- Real Marathi speakers' judgement of any Marathi output.
- Several callers at once.
