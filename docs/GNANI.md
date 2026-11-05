# Gnani AI in Vakya

Vakya hears with Gnani's **Prisma**, speaks with Gnani's **Timbre** voices and clones a voice through Gnani's voice-clone
endpoint. There is no second speech provider and no local speech model: when Gnani cannot answer, Vakya says so honestly (a short
pre-rendered Timbre line, then an offer of a callback or a person) instead of guessing with something else. Replies are written by
Embeddings Flash (Google, hosted) and documents are searched with Embeddings embeddings, keyword search as the backup; Gnani does the speech only.

The code was written without a key and first run against the live API on **5 Nov 2026**: Timbre speech and Prisma recognition
(including `keyterms`, `topic`, `speaker_names` and a substitution rule) work; two guessed fields were wrong and are fixed (see
"Engineering notes found against the real API"). The realtime stream, cloning and a real phone call have not run yet. With no
`GNANI_API_KEY` nothing reaches Gnani, and `GET /api/ready` says so.

## What is wired

| Seam | Where | Behaviour |
|---|---|---|
| HTTP client | `backend/services/gnani_client.py` | `transcribe`, `synthesize`, `clone_embedding` (httpx, pooled), `StreamingRecognizer` (WebSocket, `websockets`). 402 becomes `PROVIDER_CREDITS`, 429 `RATE_LIMITED`, 401/403 `PROVIDER_AUTH`, a timeout or unreachable host `PROVIDER_UNAVAILABLE`. The key is only ever the `X-API-Key-ID` header; it is not logged or put in an error. Each public function is a door onto the gateway; `_..._raw` does the HTTP. |
| Gateway | `backend/services/gnani_gateway.py` | The one door (below). |
| Ledger and budget | `backend/services/usage_ledger.py` | The rates, the append-only usage file, the budget guard, the summaries. |
| Held lines | `backend/services/gnani_hold_lines.py` | The pre-rendered "one moment" and "I will call you back" lines (below). |
| Provider logic | `backend/services/gnani.py` | Language-code maps, voices, keyterms, the audio glue, the acronym repair. |
| Recognition | `backend/services/asr.py` | REST `POST /stt/v3`, `format=codemix`, model `GNANI_STT_MODEL`. `language_code` is the agent's languages (at most four, `hi-IN,en-IN`; `indic-auto` when unknown or on prisma-v2.5 with several). `keyterms` are `GNANI_KEYTERMS`, the desk's learnt vocabulary, the words the expected answer brings (weekday and month names, yes and no) and the names and terms the agent's documents mention (at most 200 in all). `previous_text` is the caller's previous utterance (Gnani keeps its last 600 characters). `topic`, `speaker_names`, `enable_substitution` and `substitution_map` and the `X-API-Request-ID` header carry what the desk has learnt and what the caller is about to say: see [HEARING.md](HEARING.md). The detected language, `request_id` and `processing_time` go into the turn's hints. Browser recordings (webm) are re-encoded to WAV first. |
| Voices | engine `timbre` | `POST /api/v1/tts/inference`. `language` is always named: `hi-IN`, `mr-IN`, `hi-en` and so on. With `auto` Gnani resolves Devanagari text to Hindi (https://docs.gnani.ai/api/TTS/tts-input-formating.md), so a Marathi reply would have its numbers read the Hindi way; `gnani.tts_language_code` therefore sends `mr-IN` for Marathi, and only an unknown language with Latin text is `auto`. Talk page and web call: 24 kHz WAV. Phone: raw PCM16 at 8 kHz. Registered for hi, mr, bn, ta, te, kn, ml, gu, pa, en and `hi-en`. |
| Cloning | engine `timbre_clone` | The consent-verified recording is sent to `/api/v1/tts/voice-clone/embeddings`; the 768-number embedding is stored with the voice, sealed like the recording. Speaking uses `model: vachana-vc-v1` with `speaker_embedding`. The consent check and the receipts are untouched. |
| Receipts | `backend/services/licences.py` | `engine_licence` is "Gnani AI API terms" for `timbre` and `timbre_clone`. |

## The gateway

A Gnani account allows **one active request at a time** and answers 429 to a second. So every Prisma, Timbre and clone request in
the process goes through `gnani_gateway.execute()`. In the order a request meets it:

1. **Cache.** The same text, voice, language, speed, rate and format (TTS), or the same audio bytes and parameters (STT, clone), is
   answered from memory: no slot, no credits, recorded as a cache hit. A size-capped LRU (`GNANI_CACHE_MB`, default 64, 0 is off;
   anything larger than a quarter of the cap is not kept). It lives in memory only, so a restart empties it.
2. **Breaker.** After `GNANI_BREAKER_FAILS` (5) failed requests in a row (5xx, timeout, unreachable) the breaker for that kind opens
   for `GNANI_BREAKER_COOLDOWN_S` (30 s) and refuses at once, so a caller never waits on a dead service. Then one probe goes
   through: success closes it, failure reopens it. A 402 (out of credits) or 401/403 (key rejected) opens every kind for
   `GNANI_ACCOUNT_COOLDOWN_S` (300 s). A 4xx that the request itself caused does not count.
3. **Budget.** Desk and benchmark work may not dip into the credit reserve (below). A live call is never refused by the budget.
4. **Slot.** One request at a time, in priority order: **live call, then desk and preview, then benchmark**, first come first
   served inside a lane. Queue wait and service time are measured separately and written to the ledger.
5. **Retries.** 429 and 5xx are retried with exponential backoff (base 0.4 s, cap 4 s) scaled by random jitter (0.5 to 1.0), and a
   `Retry-After` header wins when it is longer. The slot is released while sleeping, so a live request can take it. Retries stop
   when the lane's **deadline** is spent (queue wait and sleeps included): `GNANI_LIVE_DEADLINE_S` 4 s for a live call (2
   retries), `GNANI_QUEUED_DEADLINE_S` 60 s for desk work (5 retries), `GNANI_BENCH_DEADLINE_S` 600 s. A live timeout is not
   retried (it has already spent its time).
6. **WAV header repair.** Timbre's WAV replies can carry placeholder sizes (`0xFFFFFFFF`, or 0) in the RIFF and `data` headers. They
   are rewritten from the real byte count before the audio is cached or returned, so every reader and the duration estimate agree.
7. **Ledger.** One line per request.

The lane of a request comes from the turn: `turn_begin` opens a scope (phone, web and missed-call turns are `live`, anything else
is `desk`), and `turn_context` binds the conversation id to it, so a request made before the conversation was known is still
attributed to it. Scripts outside a turn use `with gnani_gateway.scope("bench"):` or `GNANI_DEFAULT_LANE=bench`.

### When Gnani cannot answer

There is nothing to switch to. The gateway raises the same `AppError` codes the client always did (`RATE_LIMITED`,
`PROVIDER_UNAVAILABLE`, `PROVIDER_CREDITS`), with this in `details`:

| `details` | Meaning |
|---|---|
| `degraded` | Why: `busy`, `rate_limited`, `timeout`, `breaker_probe` (passing), or `breaker_open`, `server_error`, `unreachable`, `out_of_credits`, `auth_rejected` (lasting), or `budget_reserve`. |
| `action` | `hold_line` (a live call, passing problem), `callback` (a live call, Gnani is down), `queued` (any other lane: it waited its whole deadline, nothing is lost, `retry_after_s` says when to send it again), `held_back` (budget reserve). |
| `hold_line` | For a live call: `one_moment` or `callback`, the key of the line to play. |

**Non-live work is queued.** Desk, preview and benchmark requests wait for the slot in priority order for up to a minute (ten for
benchmarks) and are retried five times with backoff before they report `queued`. The queue is the slot's own priority queue, in
memory; a request that reports `queued` is not stored anywhere, the caller sends it again after `retry_after_s`.

**Held lines.** `gnani_hold_lines.for_error(error, language, phone=True)` returns the line to play for such an error (`{"line",
"text", "audio", "action"}`) and writes a `hold_line` entry to the ledger (`hold_line_played` or `callback_offered`). The audio is
Timbre's own, rendered ahead of time (`prepare()`: 2 lines x 2 languages x 2 formats, about eight short requests, run in the
background after the first served voice request unless `GNANI_PREPARE_HOLD_LINES=off`) and kept on disk in
`VAKYA_HOLD_LINES_DIR`, so it is there in the middle of an outage and after a restart. Two formats: phone (raw PCM16 at 8 kHz) and
web (24 kHz WAV). The text in the four languages that have it (en, hi, mr, hi-en) is in `LINES`; the languages prepared are
`GNANI_HOLD_LINE_LANGUAGES` (default `en,hi`). If a line has not been prepared, `audio` is `None` and `text` is still there for a
text channel or the owner's callback list. `GET /api/ready` lists what is missing.

What the turn code does with it (the contract): on a live `AppError` with `details.hold_line`, play the line, offer the caller a
callback or a person, and for `one_moment` try the request once more. The turn pipeline is owned elsewhere; this is the seam.

### The realtime stream holds the slot

The WebSocket (`StreamingRecognizer`) holds the gateway's one slot for its whole session (up to 15 minutes), because Gnani counts a
stream as the active request. The trade-off: while a stream is open, REST requests (Timbre replies in the same call, a desk
preview) wait for the slot until their deadline and then degrade as above. A live call that streams the caller's speech therefore
cannot also fetch a Timbre reply on the same account. **Until the real account proves otherwise the phone path stays on REST**
(per-utterance WAV). If a real key shows the limit counts REST requests only, set `GNANI_STREAM_HOLDS_SLOT=off` and the stream
stops taking the slot (it is still recorded in the ledger as `stt_stream`). The stream is billed at the streamed rate.

### Prisma acronyms

Prisma can return an acronym letter by letter ("O P D", "U.P.I"). `gnani.collapse_spelled_acronyms` joins runs of single capitals
into one word ("OPD", "UPI"); runs made only of A and I ("I A") are left alone because they are as likely to be the pronoun and
the article. The raw text is kept as `text_raw`. Off with `GNANI_FIX_ACRONYMS=off`.

## Usage ledger and budget

`VAKYA_USAGE_FILE` (default `backend/data/usage.jsonl`) is **append-only JSON lines**, never rewritten. Delete it to start a new
budget period. One line per request:

```
{"t":"req","ts":..,"kind":"stt|stt_stream|tts|clone|hold_line","lane":"live|desk|bench","seconds"|"chars":..,
 "credits":<estimate>,"latency_ms":<service time>,"queue_ms":..,"cache_hit":false,"ok":true,"attempts":1,
 "degraded":"busy"?,"action":"hold_line"?,"scope":..,"conversation_id":..?,"user":..?}
{"t":"bind","ts":..,"scope":..,"conversation_id":..}      a turn learns its conversation id after its first request
```

**Budget guard.** `GNANI_CREDIT_BUDGET` (default 5000) and `GNANI_CREDIT_RESERVE_PCT` (default 20). Desk and benchmark work that
would push the total past `budget x (1 - reserve%)` is **not sent**, and the error says so in plain words ("this would use credits
held in reserve for live calls: 3,990 of 5,000 credits used, 1,000 reserved"). A live call is never refused by the budget; if Gnani
itself refuses it, the held-line path above takes over. `GNANI_RUPEES_PER_CREDIT` (unset by default) adds rupee figures; without it
only credits are shown.

### Rates (unverified until measured)

Every credit figure is an **estimate** (the ledger holds estimates, not Gnani's invoice). The defaults come from another entry's
published measurements and have **not been verified on this account**:

| Rate | Default | Env | Source |
|---|---|---|---|
| Prisma REST | 0.0097 credits per second of audio | `GNANI_RATE_STT_REST_PER_S` | another entry, unverified |
| Prisma realtime stream | 0.0073 credits per second | `GNANI_RATE_STT_STREAM_PER_S` | another entry, unverified |
| Timbre | 2.9 credits per 1,000 characters | `GNANI_RATE_TTS_PER_1K_CHARS` | another entry, unverified |
| Voice-clone embedding | 0 per request | `GNANI_RATE_CLONE_PER_REQUEST` | nobody has measured it; counted as 0 |

The summary says "Estimated from default rates that nobody has measured on this account yet" until every rate that matters is set
from the environment, and "Estimated from measured rates" after.

**Measuring the rates.** Note the credit balance in Gnani's console, send N requests of known size (for example twenty 10-second
clips, then twenty 500-character texts, one kind at a time, nothing else running), note the balance again, divide the difference by
the seconds or characters sent, and put the result in the env variables. Do it once per kind; repeat after a price change.

## Endpoints

| Endpoint | What |
|---|---|
| `GET /api/ready` | Public, **never calls Gnani and never spends credits**. Whether the key is set, the gateway (slot, queue, breakers, cache, deadlines), whether the ledger is writable, the held lines prepared, the budget, which Gnani model answers each lane and its state (`ready`, `retrying`, `unavailable`, `held_back`, `needs_key`), and a list of `problems` in plain words. `ok` is false without a key, with an open breaker or an unwritable ledger. |
| `GET /api/usage/summary?days=7` | Credits and requests by kind, cache hit rate, p50 and p95 latency per kind (service time, cache hits and failures excluded), queue wait p50 and p95, times Gnani was busy or down (by reason and by action, held lines played, the last 20), cost per call and per booking, a daily series, the budget with the reserve, and the state per lane. Bench work is shown but left out of the cost per call and per booking. The bookings are those made on calls and not cancelled in the window. |
| `GET /api/usage/calls?days=30` | `{conversation_id: {credits, requests, degraded, actions}}` for the calls list, in one request. |
| `GET /api/usage/calls/{conversation_id}` | One call: its requests, credits, and the times Gnani could not answer. |

A line written during a signed-in user's turn carries that user's id and other users do not see it; lines with no user (desk
actions, benchmarks) are visible to everyone. One ledger file serves the whole installation.

The desk shows it in Settings ("Speech models": the model per lane and why, the budget bar with the reserve marked, latency, queue
wait, cache hit rate, and the history of "Gnani busy, retrying" and "Gnani unavailable, offered a callback"), on Today ("Cost per
booking", a seven-day credit sparkline, and how often Gnani was busy or down) and on each call in the calls list (credits used,
marked "estimated"). Every figure is labelled "estimated from measured rates" or "estimated from default rates, not yet measured".

## Get a key and switch it on

1. Sign up at Gnani's developer console (the Vachana API, `api.vachana.ai`) and create an API key. A 402 from the API means the
   credit is gone.
2. Put it in `backend/.env`: `GNANI_API_KEY=...`. Restart the backend.
3. Check `GET /api/ready`: `gnani_configured` is true and every lane shows `ready`.

Other variables (all optional, see `backend/.env.example`): `GNANI_STT_MODEL` (`prisma-v3.0`, `prisma-v2.5`), `GNANI_STT_FORMAT`
(`verbatim`, `transcribe`, `codemix`, `transliterate`; default `codemix`), `GNANI_TTS_MODEL` (`timbre-v2.5`), `GNANI_VOICES`
(`Name:lang,Name:lang`, default `Nalini:hi,Kaveri:en,Poorvi:hi-en`), `GNANI_KEYTERMS`, `GNANI_BASE_URL`, `GNANI_TIMEOUT_S` (20), and
the gateway variables above.

## First live run

With a key in `backend/.env`, one command makes first contact and prints every reply shape (the key is never printed):

```
backend/.venv/bin/python backend/bench/gnani_smoke.py            # voices list, Prisma on a clip, Timbre wav + raw 8 kHz
backend/.venv/bin/python backend/bench/gnani_smoke.py --clone backend/bench/data/<reference>.wav   # plus an embedding
```

A `FAILED` step names the endpoint; fix the matching `GUESS` in `services/gnani_client.py` and rerun. Then check
`GET /api/ready`, make one call, and read `GET /api/usage/summary`: the first real latencies and credits are in it. The smoke
script's voice-list probe calls the HTTP layer directly and is not in the ledger.

**Confirmed on 3 Nov 2026 without a valid key:** `https://api.vachana.ai` answers; `POST /stt/v3`, `POST /api/v1/tts/inference`,
`POST /api/v1/tts/voice-clone/embeddings` and `GET /api/v1/tts/voices` all exist (401 `INVALID_API_KEY`, not 404); the header name
is `X-API-Key-ID` (any other header name gets `MISSING_API_KEY`). Error bodies are `{"detail": {"error_code", "message",
"status_code"}}`.

## What is untested

First live contact was on **5 Nov 2026** (the notes below). Still not run live: the realtime WebSocket, the clone embedding, Timbre's
8 kHz raw audio on a real phone call, a real phone call at all, and the credit rates (the ledger still estimates from defaults; check
the balance in Gnani's console to measure them). The tests (`backend/tests/test_gnani_client.py`, `test_gnani_providers.py`,
`test_gnani_gateway.py`, `test_gnani_hold_lines.py`, `test_usage_ledger.py`, `test_usage_api.py`) use `httpx.MockTransport` and
fakes shaped like the live replies. Still guessed:

- **Embedding upload.** Multipart field named `audio_file`, reply key `speaker_embedding`.
- **Auth header on the WebSocket.** `x-api-key-id` plus the configuration headers.
- **Prisma gives no per-segment confidence**, so a turn's recognition confidence is unknown.
- **Cloning quotas and consent rules, and Timbre's latency on a phone turn.**
- **Whether the one-at-a-time limit counts a stream, a REST call, or both.**

## Engineering notes found against the real API

One clip so far (a Timbre Hindi sentence fed back to Prisma), so these are first-contact facts, not measurements.

| Date | Area | Documentation said | The API did | Change |
|---|---|---|---|---|
| 5 Nov | TTS output format | `audio_config.container` (wav, raw, mp3, ogg, mulaw, alaw) | We had sent `audio_config.format`: it was ignored and raw PCM came back for a WAV request. With `container` it returns a WAV (5.44 s, 24 kHz, mono) | `_tts_body` sends `container`, `num_channels`, `sample_width`, `encoding` |
| 5 Nov | Cloned-voice body | only `text`, `model`, `speaker_embedding`, `audio_config` | not run live yet | `_tts_body` sends only those for a clone |
| 5 Nov | STT reply shape | | The words are in `transcript`; also `success`, `request_id`, `model`, `processing_time`, `end_to_end_latency`, `detected_language_code` | `transcribe` reads `transcript` (`text` as a fallback) |
| 5 Nov | STT form fields | JSON arrays for `keyterms` and `speaker_names`; `substitution_map` as `[{"patterns": [...], "replacement": ...}]` with `enable_substitution`; `topic`; `X-API-Request-ID` | All accepted (200). Prisma first wrote "Vakya" as वाकया; key terms alone, the substitution rule alone, and speaker names with a topic each made it वाक्या | none needed |
| 5 Nov | `topic` | | On this one clip both requests with a `topic` dropped "सुबह" from "कल सुबह दस बजे"; without it the word was kept. One clip, so not yet a finding | watch it in the stress run before relying on `topic` |
| 5 Nov | Code-mix | | `hi-IN,en-IN` returned "appointment" in Latin letters inside Devanagari; with speaker names and a topic it came back in Devanagari | none |
| 5 Nov | Voice list | (not documented) | `GET /api/v1/tts/voices` answers 404 | voices come from `GNANI_VOICES` and the documented list; the smoke script's voice step is expected to fail |
| 5 Nov | Latency | | Prisma REST 0.27 to 0.30 s processing on a 5.4 s clip (about 0.9 to 3.3 s round trip from this laptop); Timbre 1.0 to 2.4 s for one 79-character sentence | one sample each, not a p50 |
| | Placeholder WAV header sizes | `0xFFFFFFFF` in RIFF and `data` sizes | not seen on the first WAV | `fix_wav_header` |
| | One active request: 429 body, `Retry-After`, does a stream count? | | not hit yet | `GNANI_STREAM_HOLDS_SLOT` |
| | Measured credits per second (REST, stream), per 1,000 characters, per embedding | 0.0097, 0.0073, 2.9, unknown | not measured | `GNANI_RATE_*` |
