<p align="center">
  <img src="frontend/public/brand/vakya-banner.png" alt="Vakya" width="100%">
</p>

# Vakya: call once, it's booked

**Clinics miss calls because the front desk is busy with the person in front of them. Vakya answers the phone in Hindi, Marathi or English, books a free slot, and sends the confirmation on WhatsApp.**

Entry for Gnani AI's *Great Indian AI Internship Challenge*. Speech: **Gnani Prisma** (speech to text) and **Timbre** (voice). Replies: a hosted language model. Telephony: **Vobiz**.

| Deliverable | Link |
| --- | --- |
| Demo video | _link to be added_ |
| LinkedIn post | _link to be added_ |
| X post | _link to be added_ |
| One-page pitch | [`docs/PITCH.md`](docs/PITCH.md) |

## What it does in 30 seconds

- Picks up a real phone call (Vobiz, Indian mobile) and says it is an AI.
- Understands Hindi, Marathi, English and mixed speech through Gnani Prisma; speaks through Gnani Timbre.
- Checks the schedule, offers a free time, reads the day, time and phone number back, and books only after the caller says yes.
- Moves or cancels a booking the same way. Verified on 8 Nov: booking A28 created, moved from 5 to 6 PM, A27 cancelled, all in Hindi, each on the first "yes".
- Creates, moves and deletes the Google Calendar event.
- Sends a WhatsApp message from an approved template (confirmed, moved, cancelled) in English, Hindi or Marathi.
- Remembers the caller by phone number: greets by name, mentions the upcoming appointment, tracks rescheduled and cancelled status.
- Gives the owner a desk to watch calls live, take one over, edit schedules, and see every booking with its proof.

## Try it

```bash
git clone https://github.com/ayushcody/vakya.git && cd vakya
./vakya setup                      # needs Python 3.10+ and Node 20+
# put your keys in backend/.env (see backend/.env.example): GNANI_API_KEY, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
./vakya start                      # API :8020, web app :3010, phone-only port :8021
open http://localhost:3010/desk/try   # a browser call, no phone needed
```

1. Open the desk, pick a front desk type (clinic is the default) and type the organisation's name.
2. Open `/desk/try`, allow the microphone, and ask for an appointment in Hindi or Marathi.
3. Give a wrong last digit of your number on purpose and hear it read back and corrected.
4. Say yes. The booking appears on the Bookings page with its proof.
5. For a real phone call, add the Vobiz settings from the table below and run `./vakya start`; it opens the tunnel for you.

Other commands: `./vakya doctor` (checks the machine and the running app), `./vakya warm` (checks keys before a demo), `./vakya test`, `./vakya stress` (the noise report card). Windows uses `vakya.ps1` (untested).

## Measured results

| What | Result | Notes |
| --- | --- | --- |
| Noise report card, Gnani Prisma v3.0 (6 Nov), 24 invented booking calls (Hindi, Marathi, Hinglish) | **100%** name, day and time heard in clean, 8 kHz, G.711, and noise at 10 and 5 dB | Scored on whether name, day and time came through |
| Same, noise at 0 dB | **96%**, 100% with keyterms | A miss: 4% of calls lost a field |
| Same, 8 kHz plus 5 dB noise | 95% (n=21), 100% with keyterms | |
| Ten-digit phone numbers in noise | **3 of 6** heard whole at 5 dB | The first field noise takes. This is why every number is read back |
| Speech recognition time | median **0.8 s** per clip | Per clip, on the report card |
| Reply start time on a live call | **about 3.7 to 6 s**; first filler audio about 1.1 s | A miss: too slow for a natural phone call. Being worked on |
| Live calls, 8 Nov | Vobiz call to an Indian mobile, A28 booked and moved, A27 cancelled, calendar event created and deleted, WhatsApp templates delivered | Hand-run checks, not a measured rate |
| Caller Theatre, scripted run | 9 of 9 scenarios, 41 of 41 booking fields | Typed text with a stand-in model. Not a speech or language-model result |
| Automated tests (`./vakya test`, 8 Nov) | **3152 backend** and **632 frontend** pass | Run against fakes of the outside services |

The noise audio is synthetic speech with simulated noise, so a real caller may score lower. Full tables: [`docs/RESULTS.md`](docs/RESULTS.md), [`docs/STRESS_REPORT.md`](docs/STRESS_REPORT.md). Reproduce: `./vakya stress --conditions all --provider gnani --keyterms`.

## Safety rules

Live now:

- The agent says it is an AI at the start of every call.
- Read-back, then commit: the booking tool answers "not done" until the day, date, time and number have been read aloud and the caller has said yes. A corrected digit is read back again.
- Availability is decided by one deterministic engine, not by the model.
- A cloned voice needs recorded consent; withdrawing consent deletes the voice.
- Callers can say "forget me"; caller profiles expire after a retention period and never hold ID, card or health details.
- Every booking keeps a signed receipt over the caller's words, the read-back and the yes ([`docs/RECEIPT_SPEC.md`](docs/RECEIPT_SPEC.md)).
- The agent can ask for a person but never chooses the number it transfers to.

Being added (rules in progress, not yet confirmed in the repository):

- A confirmation expires after 3 minutes.
- A cancellation needs at least 60 minutes' notice.
- At most 3 active bookings per phone number.
- No double booking of the same slot.
- An audit log of booking actions at `GET /api/bookings/audit`.

## Architecture

```
 Caller (phone, Vobiz, 8 kHz)            Owner (browser desk)
        |  audio                                 |  watch, take over, edit schedule
        v                                        v
 +------------------+     text      +-----------------------------+
 | Gnani Prisma     | ------------> | Turn manager                |
 | speech to text   |               | read-back then commit,      |
 +------------------+               | caller memory               |
        ^                           +--------------+--------------+
        | audio                                    | prompt, tool calls
 +------------------+                              v
 | Gnani Timbre     | <---- reply text --- Hosted language model
 | voice            |                      tools: check, offer, book, move, cancel
 +------------------+                              |
                                                   v
                              Schedule engine -> bookings -> Google Calendar
                                                          -> WhatsApp templates
                                                          -> signed receipt, audit log
```

Every Gnani request goes through one gateway (one active request, cache, circuit breaker, credit reserve, usage ledger). Longer version: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/GNANI.md`](docs/GNANI.md).

## Limitations

- **Slow replies on calls.** Reply start is about 3.7 to 6 s, with a first filler sound at about 1.1 s. Callers will notice the pause.
- **Inbound calling is built but not tested live.** The 8 Nov calls were placed by Vakya; a call to your own Vobiz number has not been tried. The missed-call callback has the same status.
- **Synthetic noise tests.** The report card used synthetic speech; no real caller recordings exist.
- **Phone numbers are the weak field in noise** (3 of 6 at 5 dB), so the read-back is not optional.
- **No second speech model.** If Gnani is out of credits or down, the caller hears a short pre-rendered line and a callback offer.
- **The language model is not Gnani's.** The caller's words and the document passages a turn needs reach its provider, and its rate limits sit in every reply path.
- **Voice cloning and the realtime Prisma stream** have not been run against the live API.
- **Hours reader** covers the English, Hindi and Marathi phrases it knows and lists what it did not understand.
- **Tested on one machine** (Apple M4, 16 GB). Windows and Linux are untested.
- **Not legal advice.** Consent, labels and deletion follow the direction of India's rules; check what is lawful for your use.

## Configuration

Settings live in `backend/.env`; [`backend/.env.example`](backend/.env.example) documents them.

| Setting | Purpose |
| --- | --- |
| `GNANI_API_KEY` | Gnani Prisma, Timbre and cloning |
| `GNANI_CREDIT_BUDGET`, `GNANI_CREDIT_RESERVE_PCT` | Credit budget and the share kept for live calls (5000 and 20) |
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | Replies from a hosted language model reached through an OpenAI-compatible endpoint; all three are set in `.env` |
| `EMBEDDINGS_API_KEY`, `EMBEDDINGS_BASE_URL`, `EMBEDDINGS_MODEL` | Hosted embeddings for document search; search falls back to keywords without them |
| `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN`, `VOBIZ_CALLER_ID` | Real phone calls; without them only the browser call works |
| `GOOGLE_CALENDAR_ID`, `GOOGLE_SERVICE_ACCOUNT_FILE` | Calendar sync |
| `CONSENT_MODE` | `tick` (default) or `strict` |
| `CALLER_PROFILES`, `CALLER_PROFILE_RETENTION_DAYS` | Caller memory and how long it is kept |
| `TOOLS_READBACK` | Read back, then commit on a yes (default on) |

## Repository map

| Path | What is in it |
| --- | --- |
| `backend/` | FastAPI app: routers, services (Gnani, schedules, tools, caller memory, telephony), eight front desk templates, benchmarks, tests |
| `frontend/` | Next.js app: public site, the desk, `/verify`, the report card |
| `docs/` | Architecture, Gnani, phone, schedules, caller memory, results, receipts, status, pitch |
| `sdk/verify/` | Offline receipt checker (Python and Node) |
| `deploy/` | Container deployment guide |
| `vakya`, `vakya.ps1` | Set up, start, check and test |

Project state: [`STATUS.md`](STATUS.md).

## Team

| Name | Role |
| --- | --- |
| Ayush Chougula | Phone pipeline, Gnani integration, Vobiz, WhatsApp, Google Calendar, schedules and booking, caller memory, the noise report card |
| Atharva | UI: the desk, the phone layout and the site |

## Licence

Source code under the [MIT Licence](LICENSE). Educational and research project, not affiliated with Gnani AI. Gnani services are used through Gnani's API under Gnani's terms with your own key; the language model and telephony are used under their providers' terms. All test data is synthetic.
