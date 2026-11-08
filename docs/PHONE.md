# Phone calls

How Vakya's voice receptionist takes and places calls over Vobiz (Vakya's only phone provider; decided 4 November 2026): setup,
inbound and outbound, the missed-call callback, the instant greeting, barge-in and turn-taking, caller memory, transfer and
takeover. Status in one line: outbound calls through Vobiz to an Indian mobile were verified live on 8 Nov 2026 (booking created and moved, another cancelled, in Hindi). Inbound calls to our own number and the missed-call callback are built and tested with fakes, not yet live. Reply start time on calls is about 3.7 to 6 s, with the first filler audio at about 1.1 s. Check each item with your own whitelisted phone first. No real caller has used this.

## Vobiz

The agent can place a call to a phone and talk, or answer a call to a Vobiz number. It speaks first (the AI disclosure), and the
caller can talk over it. How it connects: Vobiz fetches an **answer document** (VobizXML) from us when a call is answered; that
XML holds a `<Stream bidirectional="true">` element, so Vobiz opens a WebSocket to us and streams the caller's voice (mu-law, 8 kHz);
whatever we send back with `playAudio` is played to the caller, and `clearAudio` drops what is queued when the caller barges in.

**Set up (once)**
1. `backend/.env`: `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN` (Vobiz console, API credentials) and `VOBIZ_CALLER_ID` (a Vobiz number you
   own, in international format). `PHONE_PROVIDERS=vobiz` is the default and the only value that works. A number bought less than
   about two minutes ago is refused as "From number not owned by account"; try again.
2. `PUBLIC_BASE_URL`: the https address Vobiz can reach. `./vakya start` sets it from the tunnel it starts (ngrok if
   installed, else cloudflared; `VAKYA_TUNNEL=off` to skip). The tunnel points at the **phone-only port** (`PHONE_PUBLIC_PORT`,
   default 8021; `backend/phone_public.py`), which serves only the call stream, the answer document, the hangup callback and the
   missed-call link: the rest of the API (voices, cloning, keys, the call endpoint) stays on the loopback-only main port and is
   never reachable from the internet. By hand: `ngrok http 8021` (not 8020) and paste the address; it changes when ngrok
   restarts (and every address you pasted into the Vobiz console then goes stale: use a fixed tunnel for a real number). A request
   from another device on your network needs `VAKYA_LOCAL_TOKEN` (see `backend/.env.example`).
   **Your own named tunnel** (for example a Cloudflare tunnel with a fixed address): put `VAKYA_TUNNEL=off` and
   `PUBLIC_BASE_URL=https://phone.example.in` in `backend/.env`. `./vakya` reads both from the shell first, else from
   `backend/.env`, starts no tunnel of its own, and `start`, `status` and `doctor` show your address (doctor also checks it
   answers). The tunnel must point only at the phone-only port (`PHONE_PUBLIC_PORT`, default 8021), never at 8020.
3. `PHONE_ALLOWED_NUMBERS`: the numbers that may be called, for example `+919876543210`. A Vobiz trial account is expected to ring
   only numbers verified in the Vobiz console as well (not documented by Vobiz: the refusal is shown as `VOBIZ_NOT_ALLOWED`).
4. `./vakya stop` and `./vakya start`. Placing a call warms everything the call needs while the phone rings; `./vakya warm`
   still warms everything up front. With `PHONE_CLONE_VOICE=1` and a cloned voice, run one test turn first, or the first sentence
   waits for the voice engine to load.

**Call from the app:** on the desk open **Receptionist**, the **Phone number** tab, press **Phone someone**, pick the number, tick that
the person agreed, press Call. Vobiz is sent `POST /Account/{VOBIZ_AUTH_ID}/Call/` with `from`, `to`, an `answer_url` (a signed link
to `/api/phone/answer/{token}`, valid 15 minutes) and a `hangup_url` (`/api/phone/status-callback/{token}`). A 429 from Vobiz
(too many calls at once) is asked once more after about a second; every other refusal is shown as it is.

**Call from the terminal**
```bash
curl -X POST localhost:8020/api/phone/call -H 'storage-mode: local' -H 'content-type: application/json' \
  -d '{"agent_id":"<agent id>","to":"+91XXXXXXXXXX","caller_agreed":true}'
```

**Inbound (a number that takes calls).** The same tab has **Receive calls on your number**. In the Vobiz console:
1. Create an **XML Application** (Voice section) and paste the address shown as its **Answer URL**, method **POST**.
2. **Attach your number** to that application.
Vobiz then POSTs `CallUUID`, `From`, `To` and `Direction` to the Answer URL when a call arrives and gets the answer document back
(it must answer within 10 seconds; a failed webhook is retried up to three times). The caller's `From` is kept in memory, keyed by
`CallUUID`, because Vobiz's WebSocket `start` event carries no caller number: that is how a returning caller is recognised.
The link is valid 30 days; make a new one before it runs out.

**If the agent cannot take the call (fall back to a person).** When the agent already has `PHONE_MAX_CALLS` calls (default 2) it
refuses the stream before accepting it (close code 4429). Vobiz's documentation does not say what the call does then. Give the
application a **Fallback URL** in the Vobiz console whose XML dials your mobile (or the agent's transfer number), for example
`<Response><Dial><Number>+91...</Number></Dial></Response>`; that is also the answer when the computer is asleep, the tunnel is down
or the backend is stopped, if Vobiz uses the fallback for an unreachable Answer URL (`fallback_url` is a documented field of the
Call API; its use for applications is not verified). `GET /api/phone/status` shows `max_calls`, `active_calls` and this advice.
With `PHONE_TRANSFER_MODE=flow` the answer document itself adds `<Dial callerId=... timeout="30"><Number>transfer</Number></Dial>`
after the stream, so a stream the agent closes carries the call on to the transfer number (Vobiz: with `keepCallAlive` the
elements after `<Stream>` run once it disconnects; that they run when *we* close the stream is assumed, not tried).

**When the agent ends a call.** `keepCallAlive="true"` keeps the call up after the stream closes, so when the agent says goodbye,
meets an answering machine, hits `PHONE_MAX_CALL_S` or promises a callback, it also hangs the call up with
`DELETE /Account/{id}/Call/{uuid}/`. A caller who hangs up simply closes the stream (Vobiz sends no `stop` event).

Limits and notes: calls end after `PHONE_MAX_CALL_S` (default 600 s); at most 10 calls an hour; cloned voices pause a few seconds
between sentences, replies speak as soon as Timbre returns the first sentence (on live calls on 8 Nov, reply start was about 3.7 to 6 s); the code is `backend/routers/phone.py`,
`backend/services/telephony/vobiz.py`, `backend/services/phone_audio.py` and `backend/services/phone_greeting.py`. Call only people
who agreed.

**Instant greeting.** The agent's opening is always the same words: the AI disclosure ("This is an AI assistant.") and the
agent's **Greeting** field (a short "Hello! How can I help you today?" in the agent's first language when it is empty). It is
rendered once, ahead of the call, with the voice the agent's phone replies use (a Gnani Timbre voice, or the cloned voice), labelled and given one receipt per rendered clip, converted to 8 kHz (16 kHz if a stream is ever negotiated at it), and kept in memory and in `backend/data/phone_greetings/` (so a restart does not render it again). When Vobiz's `start`
arrives it is sent at once: no speech recognition, knowledge search or language model. It is stored as the conversation's first
turn (`[call connected]` for the caller, the greeting for the agent), so the model knows it was said and the first reply does not
repeat the disclosure. If the caller talks over the greeting before the disclosure has finished, nothing is stored and the first
reply says the disclosure. The clip is rendered when you place a call (with a warm-up of the language model connection, the
knowledge search, speech recognition and the voice while the phone rings), when you create an inbound link,
and a few seconds after the backend starts for agents that have taken calls. Changing the greeting or the voice
gives a new clip (the cache key covers them); the old one is removed. The log shows per call `accept->start` and
`start->first byte` in ms and which clip and receipt were played.

**Answering sooner.** The caller's sentence ends after `PHONE_END_SILENCE_MS` of silence. After `PHONE_EARLY_SILENCE_MS` (default
250) the reply already starts, speculatively: its audio is held until the full silence confirms the end of the sentence, and if
the caller goes on talking it is cancelled (nothing is sent or stored) and a new reply starts when they finish. Agents with
tools never start speculatively (a tool may book something). The log line `phone turn N latency` shows each stage in ms after the
end-of-speech event (a speculative start is negative) and the first audio after the caller's last word.

| Setting (backend/.env) | Default | What it does |
|---|---|---|
| `PHONE_END_SILENCE_MS` | 500 | silence that ends the caller's sentence |
| `PHONE_EARLY_SILENCE_MS` | 250 | silence after which the reply starts speculatively (0 or >= the end silence: never) |
| `PHONE_SPECULATIVE` | on | `on`: the whole reply runs during the pause and only its audio waits; `asr`: only speech recognition runs during the pause (a cancelled start costs no language-model call); `off` |
| `PHONE_PREWARM` | on | render greetings and warm up in the background (a call still renders its greeting when it needs one) |
| `PHONE_PREWARM_MAX` / `PHONE_PREWARM_DELAY_S` | 8 / 5 | how many agents' greetings are checked after startup, and how long after startup |
| `PHONE_WARM_TTL_S` | 60 | a warm-up of the same agent within this time only refreshes the language-model connection |
| `PHONE_GREETING_DIR` | `backend/data/phone_greetings` | where the rendered greetings are kept |

**Measuring it without a phone.** `backend/.venv/bin/python backend/bench/phone_latency.py --agent <agent id> --runs 5` plays
Vobiz against the running backend's phone WebSocket (no call is placed, Vobiz is not contacted): it streams a spoken question
(a Timbre voice) in real time and prints the median start -> first greeting byte and end of speech -> first reply byte.
Each run is stored as a phone conversation of that agent.

## Missed-call callback

"Call once, it's booked": a person rings the clinic's number and hangs up (or the line is busy), and the receptionist rings them
back a few seconds later, so nobody waits on hold or has to call twice. **Built and tested with fakes (a fake Vobiz and fake
Answer-URL requests); not yet tried live.** The callback itself reuses the outbound call path.

**How it works on Vobiz.** A Vobiz number is attached to an XML Application whose **Answer URL** is the missed-call link. When someone
rings that number, Vobiz POSTs `CallUUID`, `From`, `To`, `Direction` and `CallStatus` to the link and we answer with

```xml
<Response><Hangup reason="rejected"/></Response>
```

`<Hangup>` as the **first** element of an unanswered inbound call ends it without answering it; Vobiz's page says to use a `reason`
on an unanswered inbound call "to avoid answering (and billing) it" (https://www.vobiz.ai/docs/xml/hangup.md), so the caller is not
charged and hears the line refused (`rejected`; `busy` would play a busy tone). We record the caller (`From`) and schedule the
callback. Sources: https://www.vobiz.ai/docs/xml/hangup.md, https://www.vobiz.ai/docs/xml/request.md (the fields posted),
https://www.vobiz.ai/docs/applications.md (an application's Answer URL answers calls to its numbers; 10 second limit).
**Guessed / to verify on a real number:** that the rejected call really is unbilled and that the caller sees a rejection rather
than an error; that Vobiz sends `From` in a form we can read (we accept E.164 or a national number); whether the Vobiz plan allows
rejecting inbound calls at all. Vobiz also documents `ring_url` and `hangup_url` callbacks; this design does not need them.

**Set up (once per number).** Needs the Vobiz settings above and `PUBLIC_BASE_URL` (a fixed tunnel address: the link is signed
for 365 days but is only as good as the address in it).
1. On the desk open **Receptionist**, the **Phone number** tab. The missed-call panel shows the link to use (it is made with
   `POST /api/phone/missed-call-link`, see below).
2. In the Vobiz console create an **XML Application** whose **Answer URL** is that link (method **POST**) and **attach the number
   to it**. A number can have only one application, so use a number meant for missed calls (for example a number your own line
   forwards to when busy or unanswered), not the number that takes calls with the agent.
3. The same tab lists the last 10 missed calls and what happened to each; the Calls page marks a callback **Called back**.

**What happens.** Whatever happens, the answer is the reject document above, at once. The callback is placed `MISSED_CALL_DELAY_S`
later after checking again that nobody is on the line with that number. A bad or expired token records nothing and still rejects.
These rules decide whether the callback is placed; a skipped call is recorded with its reason:

- `MISSED_CALL_CALLBACK=off` (`disabled`): missed calls are logged and nobody is called back.
- No valid number in `From` (`bad_number`); a number outside `PHONE_ALLOWED_NUMBERS` when that list is set (`not_allowed`: a trial
  account may only ring allowed numbers); the phone line is not set up (`not_configured`).
- A call with that number is live or being placed (`call_live`).
- One callback per number in `MISSED_CALL_DEDUPE_S` (`recent_callback`), and at most 10 outbound calls an hour per owner
  (`rate_limited`, shared with calls placed from the desk).
- The link's receptionist was deleted: the owner's first receptionist calls back instead (`no_agent` when there is none).

| Setting (backend/.env) | Default | What it does |
|---|---|---|
| `MISSED_CALL_CALLBACK` | on | `on` or `off` |
| `MISSED_CALL_DELAY_S` | 8 | seconds between the hang-up and the callback |
| `MISSED_CALL_DEDUPE_S` | 600 | at most one callback per number in this time |

**Endpoints**

| Route | What it is |
|---|---|
| `POST /api/phone/missed-call-link` with `{"agent_id": ...}` | owner only. Returns `{url, method: "POST", valid_days: 365, enabled, delay_s, dedupe_s, setup}`: the signed Answer URL, built from `PUBLIC_BASE_URL` (503 `PHONE_NOT_CONFIGURED` without it) |
| `GET, POST /api/phone/missed-call/{token}` | the Answer URL of the missed-call number; also served on the phone-only port 8021. Authenticated only by the signed token (kind `missed`, valid 365 days), which opens nothing else. Form (or query): `CallUUID`, `From`, `To`. Always returns the reject XML |
| `GET /api/phone/missed-calls?limit=50` | owner only. `{enabled, delay_s, dedupe_s, events}`, newest first. One missed call has several events sharing a `hit`: `received`, then `skipped` (with `reason`), `called_back` or `failed`. Numbers are masked. Kept in `backend/data/missed_calls.jsonl` (`VAKYA_MISSED_CALLS_FILE`) |

A callback is stored as a conversation with source `missed_call`, which is what the Calls page badges. Only call people who agreed:
a person who rang a clinic and hung up has asked to be called, but nobody else should be on a missed-call link.

## Memory and turn-taking on calls

On a test call the agent asked "What are the patient details?"; the caller said a name and a number, paused, then said the day
and the time. The agent cut in during the pause, asked "Can you tell me the patient's name?" and kept asking for what it had
been told. Three causes, three fixes. Tested against fakes, and the outbound path was checked on a live call on 8 Nov: try it on your own phone first.

1. **The caller's words were thrown away.** A turn is stored (caller's words, reply, receipt) only at the end, once the end of the
   caller's speech is confirmed. When the caller spoke again while a turn was still thinking or speaking, that turn was cancelled
   before it was stored and the next turn only had the new audio. Now the audio of a turn cancelled before it was stored is kept
   and put in front of the next utterance (a 300 ms gap between the pieces), so the next turn hears and answers the whole thing.
2. **A fixed half second ends the turn.** People pause 0.7 to 1.5 s between the items of a name, a number and a date. After each
   reply the call now sets the silence that ends the caller's next utterance from what the reply asked for, and waits once more
   when the answer looks unfinished.
3. **Only a booking tool remembered anything.** The model had the dialogue and nothing else, so a name given before any tool ran
   was gone for an agent without tools. A per-conversation "caller details" form now holds what the caller said, and every turn
   of every agent (phone and website, with or without tools) is told what is in it.

| Setting (backend/.env) | Default | What it does |
|---|---|---|
| `PHONE_CARRY_UNANSWERED` | on | carry the audio of a reply cancelled before it was stored into the next turn (at most 3 pieces, under 25 s in all; a stored turn is never carried; a speculative start cancelled because the caller went on is not carried either, the detector already holds the whole utterance). The log says `phone turn N: carried unanswered caller audio (X ms) into the next turn` |
| `PHONE_ADAPTIVE_END` | on | set the end silence from the agent's last reply: `PHONE_DETAILS_END_SILENCE_MS` when it asked for several details ("details", "jaankari", two or more of name / number / date / time / e-mail / address), `PHONE_DETAIL_END_SILENCE_MS` when it asked for one of name, number, date, time, e-mail or address, else `PHONE_END_SILENCE_MS`. English, Hindi and Hinglish. A short piece (under 1.5 s) while the agent thinks is no longer ignored as "hello?" after such a request |
| `PHONE_DETAIL_END_SILENCE_MS` | 1100 | the wait after one detail was asked for |
| `PHONE_DETAILS_END_SILENCE_MS` | 1500 | the wait after several were |
| `PHONE_INCOMPLETE_EXTRA_MS` | 1200 | at the end silence, when the speculative turn's transcript ends in "and", "or", "aur", "toh", "phir", "uske baad", "matlab", "um", "uh", "haan wo" or a comma, or the agent asked for a phone number and the transcript holds 1 to 9 digits (digits said as words, in Devanagari, "double five" included), the reply is not confirmed yet: wait this long once more. If the caller goes on, the speculative reply is dropped and the whole of what they said is answered. 0 turns it off. An agent that does not reply early (it has tools, or `PHONE_SPECULATIVE=off`) gets the same check only after it asked for details (the wait above is 1100 or 1500 ms): its recording goes to speech recognition at the caller's pause (or at the end silence when there is no pause) with nothing said or stored; when the caller has finished, that result is the turn's, so there is no second recognition; when the answer looks unfinished the turn starts only after the extra wait, and the caller going on in that time gets one answer to everything. A speculative turn whose speech recognition has not finished at the end silence is confirmed as before. "The patient details" counts as a request that may include a number: 3 or more digits and fewer than 10 are an unfinished number |
| `CALLER_MEMORY` | on | keep the caller details form and tell the model on every turn: `(Details the caller already gave in this conversation: name ..., phone ..., date 2026-10-25, time 17:00. Do not ask for these again. If you still need other details, ask for all the missing ones together in one short question.)`, after the caller's words (the system prompt does not change, so the model's prompt cache keeps working). The arguments of earlier tool calls are merged in and win for the keys they share. Rules read the phone (ten digits, +91 or 0 prefix, numerals or words), the date and time (the resolver of `services/when.py`, relative to the call's local day), an e-mail, an age, and a name only with a clear cue ("my name is", "mera naam ... hai", "मेरा नाम ... है") or as the whole answer to a question about the name |
| `CALLER_MEMORY_LLM` | on | one small language-model call per caller turn, in the background once the reply is stored, for what the rules cannot read (a name with no cue, a doctor, a reason). Never when `LLM_API_KEY` is missing or in offline mode; any error or rate limit skips it. It only fills what is still empty, and its phone, date and time are checked like the rules' |
| `CALLER_MEMORY_MODEL` | the reply model | the language model used for that call (it has its own quota, apart from the reply turn). If the call fails, is cut off or is rate limited it is skipped silently, so the form is then filled by the rules alone |

The form is stored on the conversation (`caller_details`, local store) or, in cloud storage mode, in `backend/data/caller_details.json`
(mode 0600; `VAKYA_CALLER_DETAILS_FILE` moves it). Only a turn that was stored changes it, so a speculative turn that is
cancelled leaves it alone. The Calls page API (`GET /api/calls/{id}`) returns it as `details`, with the number and the e-mail masked.
The values are the caller's personal data: the log names which keys were read, never the values.

The `phone turn N latency` log line now ends with `end_wait=1500(details)`, or `400(normal)+1200(incomplete:trailing)` when the
extra wait was used, next to the first audio after the caller's last word.

## Call records, voicemail, ending and handing over

Everything in this section is tested against a fake Vobiz and a fake phone only. Nothing here has been tried on a real call, so
check each item with your own phone first.

**Call records.** Every call is kept in `backend/data/call_records.jsonl`: status, direction, agent, how it ended
(`hangup_reason`), the conversation with its transcript, and the outcome when there was one (`voicemail`, `human_requested`,
`ended_by_agent`, `transfer_requested`, `human_took_over`). The callee's number is stored masked only. Vobiz is given a `hangup_url`,
`POST /api/phone/status-callback/{token}` (a signed address; it needs `PUBLIC_BASE_URL`), and posts `CallUUID`, `CallStatus`,
`Duration`, `BillDuration` and `HangupCause` to it when the call ends (documented at https://www.vobiz.ai/docs/xml/request.md).
`GET /api/phone/calls` lists your calls, and the Call dialog polls `GET /api/phone/call/{id}`, which asks Vobiz for the live call
(`?status=live`) and, once it is over, its call detail record.

**Voicemail and key presses.** On an outbound call, the callee's first sentence is checked for an answering machine
(`VOICEMAIL_DETECT`, `VOICEMAIL_LONG_MS`): clear phrases ("leave a message after the beep", "the number you have dialled", Hindi
and Hinglish equivalents) end the call without the agent answering and the outcome is `voicemail`. A phrase a person could say ("not available") only counts after a long first sentence. A machine that plays only a beep
is not detected. Key presses are counted, never stored (they may be a card number or a code); pressing 0 flags the conversation as
needing a person. Vobiz does not document its `dtmf` event: the shape is assumed (`dtmf.digit`).

**Ending and handing over** (tools the agent can call during a phone call). Turn on the tool server **call** for the agent through the API (the
Tools switches in the agent screen do not list it yet): `PUT /api/agents/{id}` with `{"tools": ["appointments", "call"]}`, keeping the
ones it already has. `end_call` hangs up after the agent's goodbye. `transfer_call` exists only when you set a transfer
number: `PUT /api/agents/{id}/call-settings` with `{"transfer_number": "+91..."}`. The model cannot choose the number.
`PHONE_TRANSFER_MODE=callback` (default): the caller is told someone will call back, the conversation is flagged for a person and the
call record notes the masked number. `PHONE_TRANSFER_MODE=flow`: the audio stream is ended and the `<Dial>` that the answer
document puts after `<Stream>` dials the number (see above). That Vobiz runs it has not been verified. There is no screen for the transfer number or the tools yet.

**Live calls** (the Calls page, "Live now"). A call in progress is listed with **Listen in**: you hear both sides, read the
transcript, and can **Take over**: the agent stops talking and listening, you speak into the call with your microphone, and **Hand
back** (or closing the panel, or losing the connection) returns it to the agent. One person can have the call at a time and up to three
can listen. Your voice is not transcribed or saved; the conversation is flagged for a person. The browser audio and the
microphone path have only been tested with mocks, so expect to fix rough edges on a first real call.

## What a human must set in the Vobiz console

| Where | What | For |
|---|---|---|
| Account, API credentials | copy the Auth ID and Auth Token into `VOBIZ_AUTH_ID` and `VOBIZ_AUTH_TOKEN` | placing calls |
| Numbers | own a number; put it in `VOBIZ_CALLER_ID` | the caller id of outbound calls |
| Voice, XML Application (taking calls) | Answer URL = the link from **Receive calls on your number**, method POST; attach the number | inbound calls |
| Voice, XML Application (missed calls) | Answer URL = the link from the **missed-call** panel, method POST; attach a second number | missed-call callbacks |
| Application, Fallback URL (optional) | XML that dials your mobile | a person answers when the agent is busy or down |
| Account | a balance; for a trial account, verified destination numbers | any call |

Not needed: a Hangup URL on the applications (the outbound call carries its own `hangup_url`), and no whitelisting of Vakya's address
(Vobiz connects to your tunnel; Vobiz documents IP allow-listing only for the other direction).

## What is verified

| Part | Status |
|---|---|
| Call API request and answer (`request_uuid`), headers, paths, 402 / 401 / 429 / caller id errors | from Vobiz's pages; fakes only |
| Answer XML with `<Stream>`, `start` / `playAudio` / `clearAudio` / `checkpoint` / `playedStream` frames | from Vobiz's pages; fakes only |
| Hangup through the API, live call and call-detail lookups | from Vobiz's pages (the single-record response shape is a guess); fakes only |
| `<Hangup reason="rejected"/>` for missed calls, answer and hangup request fields | from Vobiz's pages; fakes only |
| `dtmf` event, the Fallback URL use, a `<Dial>` after a stream we close | assumed |
| Call logic: greeting, barge-in, turn-taking, voicemail, takeover | ran live on a previous carrier; fakes since |
