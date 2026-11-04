# Running late and the token queue

The most common call a clinic gets is "How many patients before me? Has the doctor come? Is he late?". Paper tokens do not move when the doctor runs late. Here the desk says so once, and the phone agent answers from it.

Code: `backend/services/delays.py` (state, queue, estimate, spoken lines), `backend/routers/delays.py` (API), the `queue_status` tool in `backend/services/schedule_tools.py`, tokens in `backend/services/schedule_bookings.py`. Desk screens: `frontend/src/components/queue/`. Tests: `backend/tests/test_delays.py`, `frontend/src/components/__tests__/RunningLate.test.tsx`.

## The state

Per time-slot schedule and per day: `{minutes_late 0..480, reason (at most 80 characters), set_at, now_serving_token}` in `backend/data/delays.json` (`VAKYA_DELAYS_FILE` overrides; with the schedules file moved it moves with it). Atomic write under a lock, like `schedules.json`. An entry belongs to a date: from the next day it is ignored, and the next write drops it. Nothing about a patient is stored there. Stays (`nights`) have no queue.

## Tokens

A booking's token for its day is its place (1-based) among that day's live bookings of its schedule, ordered by start (`schedule_bookings.token_map`). It is computed, never stored, so a cancellation renumbers the rest the same way for the desk and for the phone. `schedule_bookings.public(row, tokens)` adds `token` for today and later days, and passes `booked_by` and `relation` on when the stored row has them. The desk's bookings list computes the same numbers in the browser (`withTokens` in `components/desk/calls.ts`).

A walk-in is a staff booking in the first free slot after now today, source `walk_in` (`schedule_bookings.create_walk_in`), so it gets a token like anyone else.

## The estimate

```
expected_at = max(slot start + minutes_late, now + people_ahead x slot interval)   when now_serving_token is set
expected_at = slot start + minutes_late                                           otherwise
```

rounded to 5 minutes; the slot interval is `slot_minutes + buffer_minutes`. `people_ahead` counts the patient in with the doctor as one. Without a now-serving token it is the earlier tokens whose slot (moved by the delay) has not finished yet. Status: `seen` (before the token being served, or its moved slot is over), `now`, `waiting`. This is an estimate of a queue, not a promise.

## API

Same auth as `/api/schedules`.

| Route | Body | Answer |
| --- | --- | --- |
| `GET /api/schedules/{id}/today` | | the queue (below) |
| `POST /api/schedules/{id}/delay` | `{minutes_late, reason?}`, 0 clears | the queue |
| `POST /api/schedules/{id}/now-serving` | `{token}`: a number, or `"next"` | the queue |
| `POST /api/schedules/{id}/walk-in` | `{name, phone?, note?}` | `{booking, today}` (409 `NO_SLOT_LEFT` after the last slot) |
| `POST /api/schedules/{id}/notify/{booking_id}` | | the call (`status "calling"`, masked `to`, `call_sid`, `say`) |

The queue: `{schedule_id, schedule_name, date, slot_minutes, minutes_late, reason, set_at, now_serving, late_say {en, hi, mr}, count, tokens [{booking_id, token, name, phone_masked, source, slot_start, slot_end, expected_at, expected_say {en, hi, mr}, status, people_ahead, moved_minutes, can_call, booked_by?, relation?}], affected [the tokens still waiting whose time moved by 15 minutes or more, each with suggested_time], calling {ready, missing}}`.

"Call to let them know" (`notify`) rings the patient through the same path as `POST /api/phone/call` (allow-list, hourly limit, Vobiz account checks). A patient who booked with this number counts as having agreed to a call about the booking. When calling is not set up (no Vobiz keys or `PUBLIC_BASE_URL`) it answers 409 `CALLING_NOT_SET_UP` with a plain sentence, and the desk shows "Calling is not set up yet, so please tell this patient yourself." The call opens with the desk's usual greeting; the notice is added to the agent's instructions for that call only (kept in memory for 20 minutes, matched to the call by the number dialled), so on the first turn the agent says "Namaste, this is <desk>. The doctor is running about 40 minutes late today. Your 6 pm visit is now around 6:40 pm. Is that okay, or would you like another time?" in the patient's language, and can move the booking with its normal tools.

## The agent

Tool `queue_status(name?, phone?, schedule?)`: with the phone number the booking was made with (best) or the caller's name. Result when found: `{found, schedule, booking_id, name, token, slot_time, status, people_ahead, expected_at, minutes_late, reason, now_serving, tokens_today, say {en, hi, mr}}`; not found: `{found false, say, minutes_late?, late_say?}`. Two bookings with that name today and no phone: asks for the phone number. `book` for today while a delay is set adds `running_late {minutes_late, reason, say, next_step}` to its result.

While a delay is set every turn's prompt gets one line: "(Today the doctor is running 40 minutes late: reason. Mention it to callers asking about today; use queue_status for 'how many before me'.)" (`delays.prompt_block`, added in `turn_pipeline` next to the caller-profile block).

What `say` reads, for token 14 with 5 ahead and 40 minutes late, expected 6:40 pm:

- en: You are token 14; 5 patients are ahead of you. The doctor is running about 40 minutes late today, so please come by about 6:40 pm.
- hi: आपका टोकन 14 है; आपसे पहले 5 मरीज़ हैं। आज डॉक्टर लगभग 40 मिनट देरी से चल रहे हैं, इसलिए कृपया लगभग शाम 6:40 तक आइए।
- mr: तुमचा टोकन 14 आहे; तुमच्या आधी 5 रुग्ण आहेत. आज डॉक्टर सुमारे 40 मिनिटे उशिरा चालले आहेत, म्हणून कृपया सुमारे संध्याकाळी 6:40 पर्यंत या.

A schedule whose name does not mention a doctor or clinic says "we are" instead of "the doctor is".

## The desk

Today has "Queue and running late" for each time-slot schedule: chips (On time, 15, 30, 45, 60 min, Other) and a reason, "Now seeing token N" with a Next button, the day's tokens with their expected times, a walk-in form, and the list of patients moved by 15 minutes or more with "Call to let them know". A schedule's calendar shows a slim banner while it is late ("Running 40 min late · reason · Change") and today's booked cells say "now about 6:40 pm". The bookings list and drawer show "Token N" and, when the row has them, "booked by Rahul (son)".

## Not covered

The agent matches the caller by the phone number or name it is given; it does not read the number on the line by itself. Delays are not carried to another day. Nothing was measured on a live call.
