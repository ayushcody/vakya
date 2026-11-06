# Caller memory

Vakya remembers what a caller told it, in two places that work together: **this call** (so it never forgets anything said
earlier on the same call) and **this number** (so the next call, in either direction, starts where the last one stopped).

## 1. Within a call: nothing is left out

Embeddings Flash is shown the dialogue so far (`backend/services/call_notes.py`, used by `turn_messages`):

| Situation | What the model sees |
|---|---|
| The call fits `CALL_HISTORY_TOKENS` (default 3000, about a hundred short turns) | The whole call, as it was said |
| It does not fit | The latest turns that fit (at least six) plus a **call notes** block in the caller's message |

The call notes block always carries every captured detail (name, phone, date, time, the person the booking is for, the
trade's details such as a party size or a preferred stylist, and what the booking tools were given) and a compact summary of
the turns that were left out: what was booked or cancelled, what the caller agreed to or refused, what they asked, and what
the agent told them (prices, times, offers). The summary is composed in code, so no turn waits for it. When a Embeddings
key is configured, a better summary is written in the background and used from the next turn (a failure
keeps the code's summary). The tools always see the whole stored call, so an earlier booking is never lost to them.

Settings: `CALL_HISTORY_TOKENS`, `CALL_NOTES_LLM` (on|off), `CALL_NOTES_MAX_CHARS` (default 900).

### A phone number in pieces

A spoken ten-digit number is the detail recognition gets wrong most often on a phone line, so a number that arrives
incomplete is kept rather than asked for again from the start (`backend/services/digit_repair.py`, run after the rules
read each utterance in `turn_caller_memory`):

| Caller says | After | The form |
|---|---|---|
| "98765 4321" | "What is your phone number?" | `phone_partial` 987654321 (9 of 10), `phone_tries` 1 |
| "43210" | "Please say the last digits again." | `phone` 9876543210 (the new digits replace the end) |
| "98765", then "43210" | the number was asked for | `phone` 9876543210 (the pieces are joined) |
| "nahi, last wale 3 2 1 0" | a read-back of a number ending 3219 | `phone` 9876543210 (only the end changes) |
| "नाही, सुरुवातीचे 98766" | a read-back | the first five digits change |
| "nahi, 6 30 baje" | a read-back | nothing: a time is never part of a number |

A change is kept only when the result is a valid Indian mobile number. While the number is incomplete the model gets one
line ("Phone number not complete yet: heard 98765 4321, 9 of 10 digits. Ask only for the missing digits...") instead of
the number in the known details; after two tries on a phone call the line also says to offer the keypad
([PHONE.md](PHONE.md)). The read-back and the caller's yes are still needed to book. `phone_partial` and `phone_tries`
are working state, not details the caller gave: they are never in the desk's view of the call. A booking whose number was completed this way names the utterance
that completed it in its proof of booking.

## 2. Across calls: one profile per number

`backend/services/caller_profiles.py`. The key is the owner plus the number as E.164 (`+919876543210`).

| Field | What it holds |
|---|---|
| `display_name`, `preferred_language` | The caller's name and the language they use |
| `people` | The family of the number, the people booked for: `{name, relation?, age_band?, facts?, last_booking?, last_booked_at?}`, at most 8, oldest first, and `last_person` (see "One number, the whole family" below) |
| `facts` | The details of the agent's trade, `{key: value}`, at most 12 (below) |
| `last_booking`, `last_outcome`, `summary` | The last booking that was not cancelled, how the last call went, one or two sentences made in code |
| `follow_ups` | Up to three "ask next time" notes, set by the agent (`note_for_next_call`) or by the owner; asked on the next call, then dropped |
| `source` | `phone`, `web call` (the number was given in a browser call and not verified) or `owner`. A phone call from the number makes it `phone` for good |
| `calls`, `call_count`, `notice_given` | The last 30 calls, how many, and whether the greeting has already said that details are kept |

### When it is written

* **Phone calls**, inbound or outbound, when the call ends and the caller said something. Inbound: keyed by the caller's
  number. Outbound (a callback to a missed call, a reminder): keyed by the number that was dialled.
* **Web calls** (the browser link), when the visitor hangs up and gave a phone number during the call. The number is
  normalised to E.164 and the profile is marked `web call`. A web call never reads a profile back: a number someone only
  claims is not proof of who they are.
* Nothing is written for a call that ended on the greeting, or for a caller who asked to be forgotten on that call.

### What is remembered by trade

Every template (`backend/templates/agents/*.json`) has a `remember` field: who the people on a number are called, and the
details worth keeping. The agent's `template_id` picks the schema; an agent made from scratch gets a generic one.

| Trade | People | Details |
|---|---|---|
| Clinic | Patients (the family), with an age band | Preferred doctor, preferred time, usual visit, age band |
| Hotel | Guests | Room preference, usual nights, usual arrival time |
| Salon | People booked for | Preferred stylist, usual service, preferred time |
| Restaurant | Names the table is booked under | Usual party size, seating, occasion |
| Office | People who call | Matter type, documents pending |
| Institute | Students, with an age band | Course, age band |
| Service centre | People who call | Vehicle or appliance model, registration or serial id |
| Gym | Members | Goal, preferred slot |

Each detail has a type (`text`, `number` with a range, `enum` with choices, `time`) and is validated when it is read and
again when the profile is written. Rules read the plain cases (a party size, the nights of a stay, an age band, "for my son
Rohan"); Embeddings Flash, when a key is configured, reads the rest in the background after each turn and only
fills what the schema names.

### Never kept

Whatever route it comes in by (the rules, Embeddings, the owner's edit), these never reach a profile, and the model
is also told not to return them:

* Aadhaar, PAN, passport, voter id, driving licence or ration card numbers, and any 12 to 19 digit run that is not a phone number
* Card numbers, CVV, UPI or ATM PINs, OTPs, passwords
* Health conditions, symptoms and diagnoses (a conservative word list, English, Hindi and romanised). Only what the caller says
  for the booking, such as "dental check-up", is kept; "diabetes follow-up" is dropped whole
* An exact age (only an age band), a phone number inside a free-text detail

A call itself may hold such words in the conversation and its transcript; the profile never does.

### One number, the whole family

A son rings for his mother, a mother for her children. The number holds **the caller** (`display_name`, who usually rings) and
**the people they book for**, each with:

| Field | What it holds |
|---|---|
| `name` | As booked ("Sunita Kulkarni"); "Sunita" and "Sunita Kulkarni" are one person |
| `relation` | To the caller, always stored in English: mother, father, son, daughter, wife, husband, brother, sister, grandmother, grandfather, friend, child, `self` (the caller booked for themselves) or `other`. Spoken in the caller's language |
| `age_band` | child, teen, adult or senior (shown as "60+"); never an exact age |
| `facts` | That person's usual details from the trade's `remember` schema (usual doctor, usual visit), kept with them and not with the caller |
| `last_booking` | `{date, time, purpose}` of the last booking made for them |

An older profile (name, relation, age band, last booked) reads as it is and is written in this shape the next time it is saved.

**How it is learnt (end of call).** `read_call` takes who the booking was for from the booking tool's `name` (else the form's
`person`), and how they are related from, in this order: a `relation` or `booked_by` argument of the booking tool (any word
the model uses is turned into the English relation), what the caller said, else the form. What the caller says is read in
English, Hindi and Marathi, with Marathi's joined endings: "for my mother" and "मेरी माँ के लिए" and "माझ्या आईसाठी" give
mother, "mere papa ke liye" and "माझ्या वडिलांसाठी" give father, "for myself" gives self. The name on that booking is then the
patient's, not the caller's, so it does not replace `display_name`; a caller who introduced themselves ("my name is ...") keeps
their name. A booking for the caller is `self`. The trade's usual details of a booking made for somebody else go to that
person. The person booked for moves to the end of the list.

**How it is used.** The prompt block leads with the family, newest first, "This number usually books for: Sunita Kulkarni
(mother, 60+, usually Dr. Joshi, last visit 12 Oct, follow-up); Rahul Kulkarni (self)", and tells the agent to ask one short
question in the caller's language with a respectful form: "Is this for Sunita ji again, or for someone else?", and never to
volunteer one person's visits, doctor or details when the caller names someone else. A returning caller whose last booking was
for somebody else is greeted with that question in place of "how can I help?" (first name only). No question is asked on a
number that only books for its caller.

**Names for the speech engine.** `caller_profiles.known_names(conversation_id)` returns at most four names for the live call:
the caller's usual name, then the people most recently booked for, no repeats. It reads what the call opened with (a dict
lookup, no store read) and returns `[]` when the call has no known caller, profiles are off or anything fails; it never raises.

**Forgetting one person.** `forget_me` takes an optional `person` (a name or a relation as the caller says it: "forget my
father's details"): same read-back and yes, then only that person is removed, the rest of the family and the call stay, and the
same call does not write them back. Unclear or ambiguous ("Asha" when two are named Asha) is asked about, not guessed.

## 3. Using it next time

* **Greeting.** A known caller is greeted by name in their preferred language (when the agent speaks it, else the nearest it
  does). A booking still ahead is named as their appointment ("Hello Zoran, welcome back. Your appointment is tomorrow at
  3 PM."; "today" and "tomorrow" for those days, else the weekday or the date); one that has passed is the last one
  ("Namaste Sunita ji, pichli baar aapne Mangalvaar 4 baje ki appointment li thi."). A callback to a missed call greets by
  name too: "Namaste Sunita ji, aapne abhi {business} ko missed call diya tha." The first time, the greeting adds once that the
  details are kept to help next time and how to have them deleted. A number with no name gets the usual greeting.
* **Prompt.** Every turn carries a short block: name, language, people (and who was booked for last time), the trade's usual
  details, last booking and the notes, plus the number as `98xxxx3210`. The agent confirms rather than re-asks ("Same number
  as last time, 98xxxx3210?"), and still reads a booking back before it makes it.
* **Booking.** The call's form starts with the known name and number, so the booking tool is given them; the read-back and
  "Shall I book it?" rule is unchanged.
* A name that differs from the number's known name, which the caller did not introduce themselves with ("my name is ..."), is
  taken as the person the booking is for, not as a new caller.

## 4. Control

* **The caller.** "Forget me", "mera data hata do", "माझी माहिती काढून टाका": the agent calls the `forget_me` tool, reads back
  what it will delete, and deletes the number's profile only after a yes in the caller's next message. Nothing from that
  call is written either, and the rest of the call carries nothing. On a web call only the second part applies (the number was
  not verified, so an existing profile is not deleted by it).
* **The owner.** The Callers page (`/desk/callers`) shows each caller: name, masked number, language, the family as chips ("Sunita · mother") and one card per person (relation, age band, usual details, last and next booking, edit and remove), details as chips,
  last booking, bookings (upcoming first), call history and notes. The owner can edit names, details, people and notes inline,
  add a note for the next call, or forget the caller. `GET /api/callers`, `PUT|PATCH /api/callers/{id}` and `DELETE
  /api/callers/{id}`; an edit that holds something never kept is a 422 with the reason. A booking links to
  `/desk/callers?caller=<caller_ref>`, and `GET /api/bookings?caller_ref=` lists one caller's bookings. `caller_ref` is the
  profile id: the first 16 hex characters of SHA-256 of `<owner>|<E.164 number>` (`backend/services/caller_ref.py`).
* **Retention.** Unchanged: `CALLER_PROFILE_RETENTION_DAYS` (default 180) from the last call; older profiles are deleted at
  startup. Every API view shows the number masked.
* **Switches.** `CALLER_PROFILES=off` (no profiles, no greeting by name, no `forget_me`), `CALLER_MEMORY=off` (no in-call form),
  `CALLER_MEMORY_LLM=off` (rules only).

## 5. What forgetting does not touch

Call transcripts and recordings, bookings, and booking evidence are records of what happened and have their own retention.
Forgetting removes what Vakya keeps to serve the caller next time.

## 6. The live view

`frontend/src/components/memory` exports `WhatVakyaRemembers` (this call's details, then what is kept from earlier calls),
`useRememberedCaller(conversationId)`, `FactChips`, `PeopleChips` and `CallerEditor`, for the live split-screen to embed.
