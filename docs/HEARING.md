# Ears that learn

A voice agent mishears: "Dr. Kulkarni" becomes "Dr. Call Karni", "OPD" becomes "o p d". Nobody lets the business owner say so. Vakya does:
the owner corrects a line of a call (or types a correction in Settings), and the front desk listens for the right words on every later
call. The same module also tells Prisma what the caller is about to say, because the front desk has just asked for it.

Code: `backend/services/hearing.py` (store, hints, events), `hearing_expect.py` (what is being asked), `routers/hearing.py` (API),
`frontend/src/components/hearing/` (Misheard? form, Settings section, live chip). Gnani's side of it:
<https://docs.gnani.ai/api/STT/speech-to-text.md> (Prisma v3.0).

## What is stored

One JSON file per installation, `backend/data/hearing.json` (`VAKYA_HEARING_FILE` overrides it), written atomically under a lock like
`services/schedules.py`. Per front desk:

- up to **10 corrections**: `{id, heard, meant, learnt_from: {conversation_id, turn} | "owner", created_at, last_used_at, uses}`. Ten is
  Prisma's limit for `substitution_map`. At ten the API answers `409 RULES_FULL` and names the oldest unused rule; the owner then deletes
  one, names one to replace, or asks to replace the oldest unused one. "Unused" is read from `last_used_at`, set when a rule probably
  ran on a call (below), else from `created_at`.
- up to **40 vocabulary words** (`{term, added_by: "correction" | "owner"}`). A correction adds `meant` here, so Prisma is also told the
  right words as keyterms. Keyterms are limited to 200 in all: the desk's vocabulary goes first, then what the expectation brings, then the
  words the agent's documents mention.

Rules are checked: 2 to 60 characters (`heard`) and 2 to 80 (`meant`), at most eight words, not only symbols, case-insensitive duplicates
refused, and anything that looks like an identity, card or phone number (`services/id_redact.py`, or a run of six or more digits) is a
`422 ID_NUMBER`. A rule whose `heard` and `meant` differ only in case is allowed: Prisma's matching is case-insensitive, so it fixes
the case.

## API

| Route | |
|---|---|
| `GET /api/hearing/{agent_id}` | `{rules, vocabulary, rule_limit: 10, rules_used, vocabulary_limit: 40, note}` |
| `GET /api/hearing/{agent_id}/rules` | the rules only |
| `POST /api/hearing/{agent_id}/corrections` | `{heard, meant, conversation_id?, turn_index?, replace_rule_id?, evict_oldest_unused?}` gives `{rule, replaced, vocabulary_added, vocabulary_full, rules_used, message}`; `message` is "From now on Vakya listens for 'Dr. Kulkarni' on this desk." With a call the rule records where it was learnt (the call is checked: the owner's, this desk's, and `turn_index` is the index of a caller line in the call's transcript); without one it is "added by you" |
| `DELETE /api/hearing/{agent_id}/rules/{rule_id}` | forget a correction (its words stay in the vocabulary) |
| `POST /api/hearing/{agent_id}/vocabulary` | `{term}` |
| `DELETE /api/hearing/{agent_id}/vocabulary?term=` | |

Errors are the usual `{error, code, details}`: `EMPTY`, `TOO_SHORT`, `TOO_LONG`, `INVALID`, `ID_NUMBER` (422), `DUPLICATE`, `RULES_FULL`,
`VOCABULARY_FULL` (409), `NOT_FOUND`, `AGENT_NOT_FOUND` (404). Every route needs the owner of the front desk.

## What Prisma is sent

Per turn, in `turn_pipeline.gnani_turn_hints` (the form is built in `gnani_client._stt_form`):

| Field | Encoding | Source |
|---|---|---|
| `keyterms` | JSON array, at most 200 | desk vocabulary, expectation words, known names, document entities |
| `topic` | text, at most 1,600 characters | "The caller is saying their name to the front desk of a clinic called ...", plus the desk's names |
| `speaker_names` | JSON array, 1 to 4 | `caller_profiles.known_names(conversation_id)`, or none |
| `enable_substitution`, `substitution_map` | `"true"`; JSON `[{"patterns": [heard], "replacement": meant}]`, at most 10 | the desk's corrections; both omitted when there are none |
| `previous_text` | text, Gnani keeps the last 600 characters | the caller's previous utterance |
| header `X-API-Request-ID` | `<conversation id>:<turn>` (ASCII), or the turn id before the conversation is known | |

Back from Prisma: `detected_language_code`, `request_id`, `processing_time`, kept in the turn's hints (`detected_language_code`,
`request_id`, `processing_time`). The request id is a label: it is not part of the cache key.

## What the caller is about to say

`hearing_expect.detect` reads the front desk's last reply and answers `name`, `when` (a day or time), `digits`, `yes_no` or `open`
(`hints["expecting"]`, with `expecting_kinds`). The last sentence that asks for something decides. A read-back ("Is that right?",
"सही है?", "बरोबर ना?") is a yes or no even though it repeats a name, a day and a number; a bare "right?" or "ठीक है?" only counts when the
sentence is not itself asking for a detail ("Which time is okay?" asks for a time). Phrases, in `PHRASES`:

| Expecting | English | Hindi | Marathi |
|---|---|---|---|
| name | your / the patient's name, name please, may I have your name, what name, who is the appointment for, for whom | आपका नाम, नाम क्या / बताइए, किसके नाम, किस नाम से, किसके लिए, aapka naam | तुमचे / तुमचं / आपले नाव, नाव काय / सांगा, कोणाच्या नावाने, कोणासाठी |
| when | which / what day, date, time, when would, preferred time, what time, suits you | कब, कौन सा दिन / तारीख / समय, किस दिन, कितने बजे, kab, kitne baje | कधी, केव्हा, कोणत्या दिवशी / वेळी, कोणती तारीख, किती वाजता |
| digits | mobile / phone / contact number, number, digits, pin code, booking number | मोबाइल / फोन नंबर, नंबर, अंक, पिन कोड | क्रमांक, मोबाईल नंबर, नंबर, आकडे |
| yes_no | is that right / correct, shall I book / confirm, would you like me to book, confirm this, right? | सही है, ठीक है?, कन्फर्म, बुक कर दूँ, ना? | बरोबर, करू का, ठीक आहे का, चालेल का |

Per expectation Prisma gets a `topic` sentence and extra keyterms: for a day or time the weekday and month names in English, Hindi and
Marathi and words such as today, tomorrow, उद्या; for a yes or no "हाँ, नहीं, हो, नाही, बरोबर, सही, yes, no, correct"; for digits the
digit words in the three languages; for a name the names already heard on this call. Nothing is sent for open speech beyond the general
topic.

The previous reply comes from `run.asr_previous_agent_text` (the phone call keeps it) or the last agent row of `run.history`. A browser
call's first recognition happens before its history is read, so there the expectation is "open" until the call keeps that text on the run.

## Watch it think

Each turn the desk's live log (`services/call_events.py`, event type `hearing`) gets:

- `kind: "listening"`: "Listening for: a name (Sunita, Rahul)", "Listening for: a day or time", "Listening for: digits",
  "Listening for: yes or no" (nothing for open speech).
- `kind: "corrected"`, `probable: true`: "Probably heard 'Dr. Call Karni', corrected to 'Dr. Kulkarni' (rule learnt from call #12)" or
  "(rule you added)". Prisma applies the rules itself and does not return the text before them, so this is a guess from the final
  text: a rule counts as applied when its `meant` is in the transcript and its `heard` is not. A caller who simply said `meant` is
  counted too, and the label says "Probably".

The live view shows the chip with the caller's line of the same turn (`turn_id`), and a pending one until that line arrives.

## Not proven yet

Nothing here has run against a real key. Open: that Prisma reads `topic`, `speaker_names` and `substitution_map` as written; whether the
substitution applies to the text `codemix` returns for a Hindi name written in Devanagari (a rule's `heard` must be spelt as Prisma
spells it: copy it from the transcript); how much `topic` and `speaker_names` actually help (nobody has measured it: run
`backend/bench/stress_report.py` with and without); what a rule that matches nothing costs.
