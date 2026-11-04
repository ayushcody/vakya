# Caller Theatre

Ten synthetic callers phone a front desk, and the score comes from what ended up in the booking system, not from how the call sounded. The page is `/theatre` (public) and `/desk/theatre` (in the app): a card per scenario, and each opens the call as bubbles with the tool calls, the audio when there is some, and the scorer's verdict with its reasons. Failures are shown as plainly as passes.

**Synthetic callers, voices by Gnani Timbre, scored automatically** is the label of an audio run. Until the Gnani key exists, the run is a **text run (audio run pending)**: the callers' words are sent as typed text to the same turn endpoint, scoring is identical, and speech recognition, the caller voices and the line noise are not exercised. The audio run switches on by itself once `GNANI_API_KEY` is set (see "The two inputs").

## The ten scenarios

The tenth, `family-booking-clinic` (a son calls again and books for his mother), was added on 5 Nov: it passes in the text simulation (`simulate.py`, run by the tests) and has not been in a published run yet, so the results table below still covers the first nine.

Data in `backend/bench/theatre/scenarios.json`. A caller is a list of *moves*; a move is spoken when its trigger matches what the agent just said, so a different reply gives a different next line (substrings and shared word lists, how many times it offered, whether it read a given number back). Days are the first open days of the coming week, picked from the front desk's own schedule when the run starts, so a closed day is never asked for.

| # | Scenario | Front desk | Language | Line | What a pass is |
|---|---|---|---|---|---|
| 1 | Elderly caller, slow Marathi | Clinic | Marathi | G.711, speech slowed to 0.8x | consultation booked: day, time, name, phone |
| 2 | Rushed Hinglish caller, everything in one breath | Salon | Hinglish | G.711, 20 dB noise | booked from a single sentence |
| 3 | Street noise at 5 dB | Clinic | Hindi | G.711, 5 dB noise | booked; the caller repeats when the agent asks |
| 4 | One wrong phone digit | Service centre | Hindi | G.711, 15 dB noise | the agent reads the wrong number back, the caller corrects it, the booking has the right one |
| 5 | Requested slot is full | Clinic | English | G.711 | a booking already holds the slot; the agent offers the times either side; the caller takes the later; that is booked |
| 6 | Cancellation by phone | Salon | English | G.711 | the booking is found by phone number, the agent asks first, it is cancelled, nothing new is booked |
| 7 | Returning caller | Restaurant | Hinglish | G.711, 20 dB noise | the caller gives only a number; the desk knows his name from the earlier booking and books a table at the same time |
| 8 | Edge case: asks the agent to note an Aadhaar number | Clinic | Hindi | G.711 | the agent refuses, pushes back on twice, still books; the number is on the agent's side nowhere |
| 9 | Hotel: two nights | Hotel | Hindi | G.711 | a stay of two nights booked from the check-in day |

Every name, phone number and the 12-digit number are invented (the 12-digit number is not a valid Aadhaar number).

## What happens in a run

`backend/bench/theatre/runner.py`, per scenario:

1. creates a fresh front desk from the template through `POST /api/agents/from-template` and sets up the schedule state the scenario needs through `POST /api/bookings` (a taken slot, a booking to cancel, an earlier booking);
2. lets the scripted caller speak (the two inputs below), one turn at a time, each line chosen from the agent's last reply;
3. records per turn the text the agent heard, what it said, its tool calls (`tool_calls` of the turn's `done` result, plus any `tool` events the stream carries), the first-reply latency, and the audio files;
4. reads the bookings back from `GET /api/bookings`, the call from `GET /api/calls/{id}`, and scores the end state (`scorer.py`);
5. cancels the bookings the run made and deletes the front desk. (A cancelled booking stays in the booking history; the API has no hard delete.)

The runner only calls the running app's HTTP API. It never edits the turn pipeline.

### The two inputs

| | Audio run | Text run |
|---|---|---|
| When | `GNANI_API_KEY` is set in the runner's environment and the app has it too (automatic) | no key yet, or `--text` |
| Caller | Gnani Timbre voice, slowed for scenario 1 | typed text |
| Line | noise mixed in at the scenario's SNR, then the 300 to 3400 Hz band at 8 kHz and G.711 mu-law (the degradation of `bench/stress_report.py`); the SNR shown is measured on the mix | not applied |
| Sent to | `POST /api/conversations/{id}/turn/stream`, the web call's path | `POST /api/conversations/{id}/turn` with `text` |
| Recognition | Gnani Prisma | none |
| Audio on the page | caller (on the line, and before it) and agent, per turn | none |
| First-reply latency | to the first spoken answer | until the reply text is ready (leaves the spoken reply out) |

### The two model modes

* **Scripted model** (`--scripted-llm`): the app's model step is `scripted_llm.py`, a small local server standing in for Embeddings Flash, with a deterministic dialogue policy. Everything else is the real app: the tool loop, the schedule store and availability rules, the read-back (`book` answers NOT DONE until the agent read the details back and asked), confirm-before-cancel, the booking-id and unconfirmed-claim guards, caller details and the stored conversation. Only the wording and the choice of the next tool come from rules. **A scripted pass shows the path and the rules work end to end; it says nothing about what a language model does with the same calls.** Start the app against it as the runner prints when it is run without a model mode (it points the app's model address at the stand-in on port 8099), then run `backend/.venv/bin/python backend/bench/theatre/runner.py --scripted-llm`.
* **Live model** (`--allow-live`): Embeddings Flash through the app's configured `EMBEDDINGS_API_KEY`. The runner refuses to run against anything else unless `--scripted-llm` is given. No live run has been made yet.

## How a scenario is scored

From the end state, in `backend/bench/theatre/scorer.py`. A scenario passes when every *must* check passes; advisory checks are shown as warnings.

* the booking exists with the right **schedule, date, time, nights, name and phone** (the phone is taken from the arguments of the successful `book` call and checked against the masked number the booking keeps); the names match across scripts ("सुनीता कुलकर्णी" equals "Sunita Kulkarni");
* nothing extra is booked; in the cancellation call, nothing new is booked and the right booking is `cancelled`;
* scenario 4: the agent read the wrong number back **and** the booking has the corrected one ("read-back catch");
* scenario 5: the agent offered two times;
* scenario 7: the agent said the caller's name though the caller never did;
* scenario 8: the agent declined, and the number is in none of its replies, tool calls, the bookings or the caller details. Advisory: the caller's own transcript keeps the number as spoken (see "Findings");
* advisory: the agent read the day and time back and asked before it committed.

Reported per scenario: fields correct (of the booking fields the scenario names), turns to complete, first-reply latency and total time (the last two only for a live-model run: a scripted model answers instantly, so they are shown as not measured). The strip on the page: scenarios passed, fields correct, median turns, and median first reply for a live-model run.

## Results

<!-- theatre:results:begin -->
**Scripted model, text run (audio run pending), run 20261104-052003.** Scripted model: the tools, bookings and rules are the real app; the model's wording and choice of tool come from a deterministic stand-in. 9 of 9 passed; fields correct 41/41 (100.0%); first reply and total time not measured (scripted model, latency not meaningful: it answers instantly).

| # | Scenario | Language | Line | Result | Fields | Turns | First reply | Total | Why |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Elderly caller, slow Marathi | Marathi | 8 kHz G.711, clean (planned) | PASS | 5/5 | 3 / 3 | n/m | n/m | Booked doctor consultations on 2026-11-05 11:00 in 3 turns; every check passed. |
| 2 | Rushed Hinglish caller, everything in one breath | Hinglish | 8 kHz G.711, 20 dB (planned) | PASS | 5/5 | 2 / 2 | n/m | n/m | Booked appointments on 2026-11-05 17:00 in 2 turns; every check passed. |
| 3 | Street noise at 5 dB, Hindi | Hindi | 8 kHz G.711, 5 dB (planned) | PASS | 5/5 | 3 / 3 | n/m | n/m | Booked doctor consultations on 2026-11-05 17:00 in 3 turns; every check passed. |
| 4 | One wrong phone digit, caught by the read-back | Hindi | 8 kHz G.711, 15 dB (planned) | PASS | 5/5 | 4 / 4 | n/m | n/m | Booked service slots on 2026-11-05 14:00 in 4 turns; every check passed. |
| 5 | Requested slot is full, caller takes the later one | English | 8 kHz G.711, clean (planned) | PASS | 5/5 | 4 / 4 | n/m | n/m | Booked doctor consultations on 2026-11-05 18:15 in 4 turns; every check passed. |
| 6 | Cancellation by phone | English | 8 kHz G.711, clean (planned) | PASS | 1/1 | 2 / 2 | n/m | n/m | Cancelled the booking in 2 turns; every check passed. |
| 7 | Returning caller, the desk remembers him | Hinglish | 8 kHz G.711, 20 dB (planned) | PASS | 5/5 | 2 / 2 | n/m | n/m | Booked table bookings on 2026-11-05 20:00 in 2 turns; every check passed. |
| 8 | Edge case: asks the agent to note an Aadhaar number | Hindi | 8 kHz G.711, clean (planned) | PASS | 5/5 | 5 / 5 | n/m | n/m | Booked doctor consultations on 2026-11-05 10:30 in 5 turns; every check passed. |
| 9 | Hotel: two nights from next week | Hindi | 8 kHz G.711, clean (planned) | PASS | 5/5 | 3 / 3 | n/m | n/m | Booked rooms on 2026-11-05 12:00 in 3 turns; every check passed. |
<!-- theatre:results:end -->

`n/m` is not measured. The scripted model answers in a few milliseconds, so first-reply and total time say nothing about a real model and are not reported for a scripted run; they are reported only for a run with the live model.

Result files: `backend/bench/theatre/results/<timestamp>-<model mode>-<input>.json`, `latest.json` (the newest) and `latest-<model mode>.json`; audio under `results/audio/<run>/`. API (read-only, no sign-in, the data is synthetic): `GET /api/theatre/latest[?mode=]`, `GET /api/theatre/runs`, `GET /api/theatre/runs/{run_id}`, `GET /api/theatre/audio/{path}`.

## Offline checks

* `runner.py --dry-run` validates the scenario file and shows what the app's own readers (`services/when.py` for days and times) make of each opening line, and calls nothing.
* `simulate.py` runs every scenario in text, in about a second, against the real tool loop and a private schedule store with the scripted model, no API and no audio. `backend/tests/test_theatre.py` runs it for all ten, plus the loader, the branching, the scorer and the results API.

## Findings the theatre made while it was built

* **Booking names in Devanagari are refused.** `schedule_bookings.clean_name` accepts letters with `\w`-style matching, which leaves out Devanagari vowel signs, so "सुनीता कुलकर्णी" is rejected ("Please give a name (2 to 80 letters)"). A model must retry in Roman letters; the scripted model does. In an audio run, where recognition writes names in Devanagari, a live model that does not retry will fail scenarios 1, 3, 4, 8 and 9.
* **The caller's own words are stored as spoken.** Scenario 8's agent refuses and keeps the number out of its replies, tool calls, bookings and caller details, but the stored turn text contains it (no redaction of ID-like numbers). Shown as a warning on the card.
* **`services/when.py` misreads some Hindi.** "साढ़े चार बजे" is read as 15:30, and a run of spoken digit words ("नौ आठ ... पाँच पाँच") is read as a time of day; scenario 2 therefore asks for 5 pm, and the scripted model removes long numbers before it reads times.
* **A statement is enough for the read-back rule.** `readback_error` is satisfied when the previous reply names the day and the hour and contains a question mark, so "Thursday 5 pm is free. Your name and number?" counts as a read-back. The scripted model reads the whole booking back with the name and phone and asks "Shall I book it?" before it calls `book`.
* **Caller memory across calls needs the phone channel.** Returning-caller profiles are keyed by the calling number and only exist on a phone call, so scenario 7 over the web path shows the desk recognising a caller from the booking made with the same number (`find_booking`), not a stored profile.

## Limits

One run per scenario: a pass shows the path works, not how often. Every caller is synthetic and scripted in advance; no real caller was recorded. Scenario 3's noise is a mix of babble and street-like noise at 5 dB, not a recording of a street.
