# Submission answers

The text for the challenge form, kept in one place. **TBC** marks what a human must fill in or confirm before submitting (deadline 10 Nov 2026, 11:59 PM IST). Facts come from `frontend/src/components/site/submissionData.ts`; update both together.

## Title

Vakya: call once, it's booked

## One-liner

A voice front desk for Indian small businesses, led by the clinic, that answers the phone in Hindi, Marathi and English, checks the schedule, reads every number back, and books only after a spoken yes.

## Team

Ayush Chougula (primary). Teammate: **TBC** (name, role, email, as registered with the challenge). Two people at most.

## Problem

Small clinics, hotels, salons and offices in India miss calls because the front desk is busy. Callers mix Hindi, Marathi and English in one sentence, and phone lines are 8 kHz, band-limited and often noisy. A booking built on a misheard phone digit or day is worse than none, because the patient never gets the reminder.

## Solution

A caller rings the business's number (the lead story is a clinic), or gives a missed call and Vakya rings back (built and tested with fakes; not yet tried on a live Vobiz number). The agent speaks first, says it is an AI and answers from the business's own documents (Embeddings Flash writes the reply; Embeddings embeddings search the documents, with keyword search as the backup). When the caller wants a time it checks the front desk's schedule with a deterministic availability engine; if the time is taken, outside the hours or not where a slot starts, it offers the nearest free times either side. Then it reads the day, date, time and phone number back and commits only on a spoken yes. A corrected digit is read back again. A returning caller is greeted from their profile. Every clip is covered by a signed receipt, a C2PA content manifest and a file label that anyone can check at `/verify`, and each booking keeps a sealed proof of what the caller said, what was read back and the caller's yes. Staff can watch a live call and take it over. A browser web call (link or QR code) lets anyone try it without a phone. One app serves eight kinds of front desk (clinic, hotel, salon, restaurant, office, institute, service centre, gym): the owner picks a type from an illustrated gallery, names the organisation, and gets the agent with its default schedule.

## Gnani models used, and how

- **Prisma v3** (`POST /stt/v3`; realtime `wss://api.vachana.ai/stt/v3/stream`): speech recognition with `format=codemix`, `keyterms` from the business's documents and `previous_text` from the caller's last turn. REST per utterance today; the 8 kHz socket is being wired onto the phone path. Code: `backend/services/gnani_client.py`, `gnani.py`.
- **Timbre** (`POST /api/v1/tts/inference`): the agent's voice, raw PCM16 at 8 kHz straight to the phone line, 24 kHz WAV in the browser.
- **Voice cloning** (`POST /api/v1/tts/voice-clone/embeddings`, `model: vachana-vc-v1`): an owner can give the agent a consented voice; the consent tick, the label and the receipt stay in front of it.

**Honest status:** all three are integrated and tested against fakes; **none has been run against the live API** because our key has not arrived. **TBC:** after the first live run (`backend/bench/gnani_smoke.py`), update this section, `docs/GNANI.md` and the site with what the real API did and what had to change. Every Gnani request goes through one gateway (one active request at a time, a cache, a circuit breaker, a credit budget with a reserve for live calls, a usage ledger), and when Gnani is busy or down the caller hears a short pre-rendered Timbre line and is offered a callback. There is no local or second speech model. Reasoning is **Embeddings Flash** (Google, hosted), not Gnani's; Embeddings embeddings search the documents, with keyword search as the backup.

## What is novel

- **The read-back as a product rule**, driven by the nature of the line: phone audio is 8 kHz and noisy and a digit is easy to mishear, so the agent never trusts a transcript for a number or date and commits only on a yes. How often Prisma hears a number whole is what the report card will measure.
- **An acoustic-stress report card** designed to be published with its failures: the same 24 invented booking calls played clean, over an 8 kHz band, in street noise at 10, 5 and 0 dB and through G.711, with and without keyterms, scored on task completion (name, day and time heard) with accepted spellings in both scripts, not on word error alone. **The Prisma run is pending; the page is empty and says so.**
- **Caller Theatre:** nine scripted synthetic callers phone a front desk and are scored from what ended up in the booking system, not from how the call sounded. 9 of 9 passed on a scripted model (text run, latency not meaningful); the audio run and a Embeddings run are pending.
- **Proof of booking and Watch it think:** a signed receipt per booking over the caller's words, the read-back and the yes; a split screen shows the agent's tool calls and what it remembers while a call is live.
- **A Gnani gateway and cost meter:** credits per call and per booking, estimated and labelled as estimates until the rates are measured.
- **Provenance on a phone voice**: consent record, AI label, C2PA content manifest, signed receipt and a public verify page, with the limits of each stated (`docs/REVOCABLE_PROVENANCE.md`).
- **Deterministic availability with nearest alternatives.** Whether a time is free is decided by plain code (`services/availability.py`), not by the model, and the same engine answers the calendar, the "Ask like a caller" tester, a staff booking and the agent's tools. A refusal always carries a reason (closed, full, blocked, outside the hours, too soon, not where a slot starts) and the nearest free slot before and after, with the sentence to say in English and Hindi ("We book every 20 minutes. 10:20 or 10:40, which suits you?").
- **A plain-language hours parser in English, Hindi and Marathi.** "Mon to Sat 10 to 1 and 5 to 9, Sunday closed" or "सोमवार से शनिवार सुबह 10 से 2" becomes a validated week, with breaks, a slot length and closed dates; guesses are flagged and the owner reviews the result. Rules first; a language model is only a fallback.
- **Visual schedules.** Per-schedule cards, a week calendar where a drag blocks a range, a month grid of rooms left for hotels, and a one-page schedule form with a live preview, for eight trades from one set of templates.
- **Turn-taking for digits**: the agent waits longer after asking for a number or date and treats a partial number as unfinished.

We make no claim that any of these is unique in the world; we looked for the same combination and did not find it.

## Measured results

Almost nothing is measured on the current stack, and we say so. **Caller Theatre, scripted model, text run: 9 of 9 scenarios passed, 41 of 41 booking fields correct** (the model step was a deterministic stand-in and no audio ran: it shows the tools and rules work end to end, not what a language model does). **Everything else is pending on Gnani:** the acoustic-stress report card on Prisma (`docs/STRESS_REPORT.md`), Prisma and Timbre latency, credits per call. See `docs/RESULTS.md` for the full list. **TBC:** after the key arrives, run `./vakya stress --conditions all --provider gnani --keyterms` and fill `frontend/src/components/report/reportCardResults.json`.

## Limitations

Gnani models not yet run live, so the report card is empty. Synthetic audio and simulated noise. Caller Theatre ran on a scripted model. The missed-call callback is built and tested with fakes, not yet tried on a live Vobiz number. Inbound calls on your own number and real Google Calendar sync are untested live. The Vobiz line has carried no real call yet; the call logic ran live on an earlier trial line. The agent's use of the scheduling tools has only been tested with a scripted model, not yet with a real model on a real call. The hours reader covers the English, Hindi and Marathi phrases it knows and asks the owner to review. Tested on one machine (Apple M4, 16 GB). No real caller, business or government API is involved. Not legal advice.

## Links

| What | Link |
| --- | --- |
| Repository | https://github.com/ayushcody/vakya (private; **TBC:** make public before submitting) |
| 60-second demo video | **TBC** (due 9 Nov; file goes to `frontend/public/landing/demo.mp4`) |
| Live demo | **TBC** (see `docs/DEPLOY_DEMO.md`) |
| LinkedIn post (nominated) | **TBC** |
| X post (nominated) | **TBC** |
| Report card | `/report-card` on the site (run pending), and `docs/STRESS_REPORT.md` |
