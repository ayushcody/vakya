# Submission answers

The text for the challenge form, kept in one place. **TBC** marks what a human must fill in or confirm before submitting (deadline 10 Nov 2026, 11:59 PM IST). Facts come from `frontend/src/components/site/submissionData.ts`; update both together.

## Title

Vakya: call once, it's booked

## One-liner

A voice front desk for Indian small businesses, led by the clinic, that answers the phone in Hindi, Marathi and English, checks the schedule, reads every number back, and books only after a spoken yes.

## Team

- **Ayush Chougula** (primary registrant): the phone pipeline and every integration: Gnani (Prisma, Timbre, cloning, the gateway), Vobiz telephony, WhatsApp confirmations and Google Calendar sync; schedules and the booking rules, caller memory, and the noise report card.
- **Atharva** (teammate): the UI. The desk (Today, Bookings, Schedules, Calls, Front desk, Proof), the desk on a phone, and the public site. **TBC:** his surname and registered email as on the challenge form.

## Problem

Small clinics, hotels, salons and offices in India miss calls because the front desk is busy. Callers mix Hindi, Marathi and English in one sentence, and phone lines are 8 kHz, band-limited and often noisy. A booking built on a misheard phone digit or day is worse than none, because the patient never gets the reminder.

## Solution

A caller rings the business's number (the lead story is a clinic), or gives a missed call and Vakya rings back (built and tested with fakes; not yet tried live). The agent speaks first, says it is an AI and answers from the business's own documents (the hosted language model writes the reply; hosted embeddings search the documents, with keyword search as the backup). When the caller wants a time it checks the front desk's schedule with a deterministic availability engine; if the time is taken, outside the hours or not where a slot starts, it offers the nearest free times either side. Then it reads the day, date, time and phone number back and commits only on a spoken yes. A corrected digit is read back again. A returning caller is greeted from their profile. Every clip is covered by a signed receipt, a C2PA content manifest and a file label that anyone can check at `/verify`, and each booking keeps a sealed proof of what the caller said, what was read back and the caller's yes. Staff can watch a live call and take it over. A browser web call (link or QR code) lets anyone try it without a phone. One app serves eight kinds of front desk (clinic, hotel, salon, restaurant, office, institute, service centre, gym): the owner picks a type from an illustrated gallery, names the organisation, and gets the agent with its default schedule.

## Gnani models used, and how

- **Prisma v3** (`POST /stt/v3`; realtime `wss://api.vachana.ai/stt/v3/stream`): speech recognition with `format=codemix`, `keyterms` from the business's documents and `previous_text` from the caller's last turn. REST per utterance today; the 8 kHz socket is being wired onto the phone path. Code: `backend/services/gnani_client.py`, `gnani.py`.
- **Timbre** (`POST /api/v1/tts/inference`): the agent's voice, raw PCM16 at 8 kHz straight to the phone line, 24 kHz WAV in the browser.
- **Voice cloning** (`POST /api/v1/tts/voice-clone/embeddings`, `model: vachana-vc-v1`): an owner can give the agent a consented voice; the consent tick, the label and the receipt stay in front of it.

**Honest status:** Prisma and Timbre have run against the live API since 5 Nov 2026. Codemix recognition with key terms, a topic, speaker names and a substitution rule works; two request fields we had guessed were wrong and are fixed (`docs/GNANI.md`, "Engineering notes found against the real API"). The acoustic-stress report card ran on Prisma v3.0 on 6 Nov (see Measured results). **Verified live on 8 Nov 2026:** real calls through Vobiz to an Indian mobile, a booking created, moved and another cancelled in Hindi, a Google Calendar event created and deleted, WhatsApp templates delivered. **Not yet run live:** the realtime stream and voice cloning. Every Gnani request goes through one gateway (one active request at a time, a cache, a circuit breaker, a credit budget with a reserve for live calls, a usage ledger), and when Gnani is busy or down the caller hears a short pre-rendered Timbre line and is offered a callback. There is no local or second speech model. Reasoning is a hosted language model, not Gnani's; hosted embeddings search the documents, with keyword search as the backup.

## What is novel

- **The read-back as a product rule**, driven by the nature of the line: phone audio is 8 kHz and noisy and a digit is easy to mishear, so the agent never trusts a transcript for a number or date and commits only on a yes. The report card bears this out: ten-digit phone numbers are the first field street noise takes (3 of 6 heard whole at 5 dB), while names, days and times survived.
- **An acoustic-stress report card** designed to be published with its failures: the same 24 invented booking calls played clean, over an 8 kHz band, in street noise at 10, 5 and 0 dB and through G.711, with and without keyterms, scored on task completion (name, day and time heard) with accepted spellings in both scripts, not on word error alone. **Measured on Gnani Prisma v3.0 on 6 Nov 2026: 100% task completion down to 5 dB street noise, 96% at 0 dB (100% with keyterms).**
- **Caller Theatre:** ten scripted synthetic callers phone a front desk and are scored from what ended up in the booking system, not from how the call sounded. 9 of 9 passed on a scripted model in the published run, made before the tenth scenario was added (text run, latency not meaningful); the audio run and a run on a live language model are pending.
- **Proof of booking and Watch it think:** a signed receipt per booking over the caller's words, the read-back and the yes; a split screen shows the agent's tool calls and what it remembers while a call is live.
- **A Gnani gateway and cost meter:** credits per call and per booking, estimated and labelled as estimates until the rates are measured.
- **Provenance on a phone voice**: consent record, AI label, C2PA content manifest, signed receipt and a public verify page, with the limits of each stated (`docs/REVOCABLE_PROVENANCE.md`).
- **Deterministic availability with nearest alternatives.** Whether a time is free is decided by plain code (`services/availability.py`), not by the model, and the same engine answers the calendar, the "Ask like a caller" tester, a staff booking and the agent's tools. A refusal always carries a reason (closed, full, blocked, outside the hours, too soon, not where a slot starts) and the nearest free slot before and after, with the sentence to say in English and Hindi ("We book every 20 minutes. 10:20 or 10:40, which suits you?").
- **A plain-language hours parser in English, Hindi and Marathi.** "Mon to Sat 10 to 1 and 5 to 9, Sunday closed" or "सोमवार से शनिवार सुबह 10 से 2" becomes a validated week, with breaks, a slot length and closed dates; guesses are flagged and the owner reviews the result. Rules first; a language model is only a fallback.
- **Visual schedules.** Per-schedule cards, a week calendar where a drag blocks a range, a month grid of rooms left for hotels, and a one-page schedule form with a live preview, for eight trades from one set of templates.
- **Turn-taking for digits**: the agent waits longer after asking for a number or date and treats a partial number as unfinished.

We make no claim that any of these is unique in the world; we looked for the same combination and did not find it.

## Measured results

Two things are measured, both on synthetic data.

- **Acoustic-stress report card on Gnani Prisma v3.0 (codemix), 6 Nov 2026.** 24 invented booking calls (8 Hindi, 8 Marathi, 8 Hinglish) spoken by Gnani Timbre, played clean, over an 8 kHz band, through G.711 and in street noise at 10, 5 and 0 dB. Task completion (name, day and time all heard): **100% in every condition down to 5 dB noise, 96% at 0 dB (100% with keyterms)**, median 0.8 s per clip. Ten-digit phone numbers are the first field noise takes (3 of 6 heard whole at 5 dB). Synthetic speech and simulated noise: it shows the shape, not what a real caller will score (`docs/STRESS_REPORT.md`, `/report-card`).
- **Caller Theatre, scripted model, text run: 9 of 9 scenarios passed, 41 of 41 booking fields correct.** The model step was a deterministic stand-in and no audio ran: it shows the tools and rules work end to end, not what a language model does.

**Still pending:** Prisma and Timbre latency on a live phone turn, credits per call and per booking, the Caller Theatre audio run, and a Caller Theatre run with a real model. See `docs/RESULTS.md` for the full list.

## Limitations

The report card is measured on synthetic calls, not recorded ones. Synthetic audio and simulated noise. Caller Theatre ran on a scripted model. Reply start time on live calls is about 3.7 to 6 s, with first filler audio at about 1.1 s. The missed-call callback and inbound calls on our own number are built and tested with fakes, not yet live. Outbound Vobiz calls, Google Calendar sync and WhatsApp templates were verified live on 8 Nov 2026. Gnani voice cloning and the realtime Prisma stream have not run against the live API. The agent's use of the scheduling tools was tested with a scripted model and then on live calls on 8 Nov 2026 with a hosted language model. The hours reader covers the English, Hindi and Marathi phrases it knows and asks the owner to review. Tested on one machine (Apple M4, 16 GB). No real caller, business or government API is involved. Not legal advice.

## Links

| What | Link |
| --- | --- |
| Repository | https://github.com/ayushcody/vakya (private; **TBC:** make public before submitting) |
| 60-second demo video | **TBC** (due 9 Nov; file goes to `frontend/public/landing/demo.mp4`) |
| Live demo | **TBC** (see `docs/DEPLOY_DEMO.md`) |
| LinkedIn post (nominated) | **TBC** |
| X post (nominated) | **TBC** |
| Report card | `/report-card` on the site (Gnani Prisma, 6 Nov 2026), and `docs/STRESS_REPORT.md` |
