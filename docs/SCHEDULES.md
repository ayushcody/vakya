# Schedules: how a front desk knows what is free

A front desk (an agent) can be booked for one or more **schedules**: "Doctor consultations" in 15-minute slots, "Rooms" by the night, "Table bookings" with eight tables. One piece of code, `backend/services/availability.py`, answers "is this time free?" for the calendar on the desk, the "ask like a caller" tester, a staff booking and the agent on the phone. They cannot disagree.

| Where | File |
|---|---|
| The model, validation and the store | `backend/services/schedules.py` (data file `backend/data/schedules.json`, override with `VAKYA_SCHEDULES_FILE`) |
| The rules (slots, statuses, nearest free times, spoken answers) | `backend/services/availability.py` |
| Bookings against a schedule | `backend/services/schedule_bookings.py` (rows live in the appointments store, `VAKYA_APPOINTMENTS_FILE`) |
| The agent's tools | `backend/services/schedule_tools.py` |
| Opening hours from words | `backend/services/hours_parser.py` |
| Routes | `backend/routers/schedules.py`, `backend/routers/bookings.py` |
| Front-desk templates | `backend/templates/agents/{clinic,hotel,salon,restaurant,office,institute,service_centre,fitness}.json` + `.md` |

Times are local to the schedule's `timezone` (`VAKYA_TIMEZONE`, default `Asia/Kolkata`) and handled as naive datetimes; the API adds the offset. Daylight-saving time is not modelled (India has none).

## 1. The model

```
Schedule {
  id "S1", agent_id, name, description,
  kind "slots" | "nights",  accent,  timezone,
  slot_minutes 5..240,  buffer_minutes 0..120,  capacity 1..500,
  week { mon..sun: [ {start "HH:MM", end "HH:MM"} ] },     [] = closed that day; a break is the gap between two ranges
  check_in, check_out   (nights only, "HH:MM"; check_out at or before check_in),
  min_notice_minutes (default 60, 0..10080),  horizon_days (default 30, 1..365),
  closed_dates ["YYYY-MM-DD"],
  created_at, updated_at
}
Block { id "K1", schedule_id, start, end, reason }          staff marked a range busy; one row per range
```

Validation (`schedules.normalise_schedule`, used by create and update, and by `parse-hours` for its week):

* `week`: every day present, up to 6 ranges a day, `"H:MM"` is zero-padded, ranges sorted, each ending after it starts, no two overlapping (touching is fine). `"24:00"` is the end of the day and `"23:59"` is read as `"24:00"`. Unknown day names, bad times and overlaps are `422` with the day named.
* Numbers outside their range, an unknown `kind`, `accent` (one of `violet orange blue green rose amber teal slate`) or `timezone`, an empty name and a malformed `closed_dates` entry are `422` with the field named.
* A `nights` schedule needs `check_in` and `check_out`, and `check_out <= check_in` so one night never overlaps the next.
* `PUT` keeps every field it does not mention.

## 2. The availability rules

**Slots.** Inside each open range the first slot starts when the range does; the next starts `slot_minutes + buffer_minutes` after the one before (the buffer widens the step: a 20-minute slot with a 10-minute buffer starts at 10:00, 10:30, 11:00). A slot must end inside its range, so a break, a closing time or the end of a range ends the run (45-minute slots in 10:00 to 12:00 start at 10:00 and 10:45 only).

**Status of a slot**, in this order:

| Status | Meaning |
|---|---|
| `past` | its start is not after now (a slot that has begun is past) |
| `blocked` | a block overlaps it (a block takes the whole capacity), or an event of the owner's own Google Calendar does (the slot then also says `calendar: true`; see `CALENDAR.md`) |
| `full` | as many live bookings overlap it as the capacity |
| `booked` | at least one booking, room left |
| `free` | none of the above |

`remaining` is the capacity left (0 when blocked). A booking counts against every slot it overlaps, so changing the slot length later cannot double-book. Cancelled bookings never count. Party size is recorded on a booking but one booking uses one unit of capacity.

**Nights.** One slot per date: `start` is check-in that day, `end` is check-out the next. A stay of N nights from date D needs a room on each of D, D+1, ... D+N-1. A block blocks the nights it overlaps. `past` means the night's date is before today (tonight can still be booked); there is no minimum notice for stays. A date is open when its weekday has at least one range (the hotel template uses `00:00` to `24:00` on every day) and it is not in `closed_dates`.

**`check()`** (the `/check` route and the agent's `check_availability`) decides in this order and returns the first reason that applies:

`past` (the start is not after now) → `too_far` (after `horizon_days`; callers only) → `closed` (a closed date or a weekday with no ranges) → `outside_hours` (no slot contains the time: a break, before opening, after closing) → `not_a_start` (the time is understood and inside the hours but is not where a slot starts, e.g. 10:30 on a 20-minute grid) → `unclear` (the date or time is missing) → `too_soon` (inside the minimum notice; callers only) → `blocked` → `calendar_busy` (an event on the owner's Google Calendar covers it; only with a calendar set up, time slots only) → `full`. Otherwise it is available.

When it is not available, `before` and `after` are the **nearest free slots** a caller could book: `before` is the latest one that starts earlier than the time asked, `after` the earliest that starts later, each searched on the same day first and then day by day (backwards to today, forwards to the horizon). They respect the minimum notice and the horizon. For a stay they are the nearest earlier and later check-in dates with room for the same number of nights. Staff bookings (`caller=False`) are not held to the minimum notice or the horizon.

`spoken` is the answer in a sentence or two, in English and Hindi, ready to say. Relative days are used for today and tomorrow ("आज", "कल"), weekday names within a week, a date after that. Hindi clock words: "शाम छह बजे", "साढ़े पाँच बजे", "सवा दस बजे", "पौने सात बजे", "डेढ़ बजे", "ढाई बजे".

Not modelled: ranges across midnight (use two days), daylight-saving time, party size against capacity, per-day holiday hours (use `closed_dates`).

## 3. The API

All routes use the same auth as the other desk routes. Errors are `{"error", "code", "details", "detail"}`.

### Schedules

`GET /api/schedules?agent_id=` returns every schedule with a `summary` for its card:

```json
{"schedules": [{"id": "S1", "agent_id": "…", "name": "Doctor consultations", "kind": "slots", "accent": "teal",
  "slot_minutes": 15, "capacity": 1, "week": {"mon": [{"start": "10:00", "end": "13:00"}, {"start": "17:00", "end": "20:00"}], "…": "…", "sun": []},
  "summary": {"today_free": 12, "today_booked": 2, "next_free": "2026-11-03T10:00:00+05:30",
              "hours_text": "Mon to Sat, 10:00 to 13:00 and 17:00 to 20:00"}}]}
```

`today_free` is the number of today's slots a caller could still book (a stay: the rooms left tonight), `today_booked` the bookings starting today, `next_free` the first slot a caller could book.

* `POST /api/schedules` (a schedule without id or timestamps; every field but `name` has a default) → `{schedule}`
* `GET /api/schedules/{id}` → `{schedule}` · `PUT /api/schedules/{id}` → `{schedule}`
* `DELETE /api/schedules/{id}` → `409 HAS_BOOKINGS` (`details.count`) while upcoming bookings exist; `?force=1` cancels them (and takes them off Google Calendar) and deletes the schedule and its blocks → `{"deleted": true, "cancelled_bookings": 2}`. Deleting an agent deletes its schedules (the bookings stay as history).

### The calendar

`GET /api/schedules/{id}/slots?from=2026-11-03&to=2026-11-09` (inclusive, at most 42 days; defaults to today and the next 6 days):

```json
{"timezone": "Asia/Kolkata", "kind": "slots", "days": [
  {"date": "2026-11-05", "open": true, "slots": [
    {"start": "2026-11-05T10:00:00+05:30", "end": "2026-11-05T10:15:00+05:30", "status": "full", "remaining": 0,
     "bookings": [{"id": "A10", "name": "Asha Rao"}], "block_id": null},
    {"start": "2026-11-05T10:15:00+05:30", "end": "2026-11-05T10:30:00+05:30", "status": "blocked", "remaining": 0,
     "bookings": [], "block_id": "K1"}]},
  {"date": "2026-11-08", "open": false, "slots": []}]}
```

For `nights` there is one slot per date (`start` = check-in that date, `end` = check-out the next).

### Blocks

* `POST /api/schedules/{id}/blocks {"start", "end", "reason"?}` → `{block}` (`{"id": "K1", "schedule_id": "S1", "start": "2026-11-05T10:00", "end": "2026-11-05T10:30", "reason": "Doctor away"}`). A range is one row. An identical block is returned instead of duplicated. `start` and `end` may carry an offset.
* `DELETE /api/schedules/{id}/blocks/{block_id}` → `{"deleted": true}`.

### Is it free?

`POST /api/schedules/{id}/check` with `{date?, time?, nights?, party?, text?}`. `text` is read by `services/when.py` for whatever the explicit fields leave out, so `{"text": "Thursday 6 pm"}` works; explicit fields win. `date` and `time` also accept words ("kal", "6 pm").

```json
{"available": false, "reason": "full",
 "requested": {"start": "2026-11-05T18:00:00+05:30", "end": "2026-11-05T18:30:00+05:30"},
 "before": {"start": "2026-11-05T17:30:00+05:30", "end": "2026-11-05T18:00:00+05:30"},
 "after":  {"start": "2026-11-05T18:30:00+05:30", "end": "2026-11-05T19:00:00+05:30"},
 "spoken": {"en": "Thursday at 6 pm is already taken. 5:30 pm or 6:30 pm, which would suit you?",
            "hi": "गुरुवार शाम छह बजे वाला समय भरा हुआ है। साढ़े पाँच बजे या साढ़े छह बजे, कौन सा ठीक रहेगा?"}}
```

`reason` is `null`, `closed`, `full`, `blocked`, `calendar_busy`, `past`, `outside_hours`, `too_soon`, `too_far`, `not_a_start` or `unclear`. For a stay the answer also carries `nights`, and `before` and `after` are check-in dates. `requested` is `null` when no date could be read.

`not_a_start` and `unclear` are different on purpose. `unclear` means the words could not be turned into a date and time ("blah", or a day with no time), so `requested` may be `null` and the answer asks what is missing. `not_a_start` means the request was understood and is inside the opening hours, but no slot starts then: `requested` is set, `before` and `after` are the slot starts on either side (10:20 and 10:40 for 10:30 on a 20-minute grid, or the nearest free ones when those are taken), and `spoken` says so plainly: "We book every 20 minutes. 10:20 am or 10:40 am, which would suit you?" / "हम हर 20 मिनट पर बुकिंग करते हैं। …". The step is the slot length plus the gap between bookings. The agent's `check_availability` returns the same `reason`, `before`, `after` and `say`. Staff bookings of such a time are refused with `409 SLOT_UNAVAILABLE` and `details.reason = "not_a_start"`.

What the text reader understands (`when.resolve_request`): English ("is Thursday 6 pm free", "next Monday 4 pm", "tomorrow at 10:30 am", "27 October 4 pm", "half past ten"), Hinglish ("kal shaam 6 baje", "parso subah 10", "agle somvaar saade paanch baje"), Hindi ("कल शाम 6 बजे", "परसों सुबह 10 बजे", "रविवार सुबह साढ़े नौ बजे"), Marathi ("उद्या संध्याकाळी 6 वाजता", "परवा सकाळी 10 वाजता", "शनिवारी दुपारी 3 वाजता", "साडेदहा"), the number of nights ("2 nights from 12 November", "do raat") and a party size ("table for 4 people"). A bare weekday that is today counts as today only while the time has not passed. Hours 1 to 7 without am or pm are afternoon, 8 to 11 morning, like the rest of the app.

### Bookings

* `POST /api/bookings {schedule_id, start, name, phone?, note?, nights?, party?}` → `{booking}`. For a time slot `start` is the slot's start; for a stay it is the check-in date. Staff are not held to the minimum notice or the horizon but cannot book a past, blocked, closed or full time. When the time is not free the answer is `409 SLOT_UNAVAILABLE` with `details {reason, before, after, spoken}`; bad input is `422` (`INVALID_NAME`, `INVALID_PHONE`, `INVALID_NIGHTS`, `INVALID_PARTY`, `INVALID_DATETIME`).
* `GET /api/bookings?schedule_id=&agent_id=&from=&to=` lists them. Every row carries `schedule_id`, `schedule_name`, `end`, `nights`, `party` and `note` next to the older fields (`service` is the schedule's name; `schedule_id` is `null` for a doctor booking of the older tool, whose `end` is start plus 30 minutes). The phone number is always masked (`••••3210`).
* `PATCH /api/bookings/{id} {start, nights?}` moves a booking to another time and returns `{booking}` in the same shape. `start` is the new time of a slot (a stay: the new check-in day). The booking keeps its id; staff are not held to the minimum notice or the horizon. When the new time is taken the answer is `409 SLOT_UNAVAILABLE` with `details {reason, before, after, spoken}` and the booking stays exactly as it was; `404 NOT_FOUND`, `409 ALREADY_CANCELLED`, `409 SAME_TIME`, `422 NOT_MOVABLE` (an older doctor booking that is not on a schedule) and `422` for bad input are worded for the screen. The Bookings page has a "Move" button next to "Cancel booking" and shows "Moved from Thu 5 Nov, 3 pm" on a moved booking.
* `DELETE /api/bookings/{id}` cancels any booking and takes it off Google Calendar, as before.

**Moving a booking** (`schedule_bookings.reschedule`, used by the desk and by the agent's `reschedule` tool) is one transaction of the appointments store. It finds the booking (the rules of cancel: this front desk's own, not cancelled, a caller cannot move one that has passed), asks the availability engine about the new time with the booking itself left out (so 3 pm to 3:30 pm on a 60-minute slot is not refused for overlapping itself), and only if that is free changes the same row in place: `date`, `weekday`, `time`, `start`, `end`. If it is not free nothing is written and `BookingRefused` carries the nearest free times exactly as for a new booking. The row records `moved_from {date, time, start}` (where it was last), `moved_at` and `version`: 1 when made, +1 on every move and on cancel (the WhatsApp confirmations use it as an idempotency key). The calendar event is then moved (`PATCH`, description gets "Moved from Thu 5 Nov 3:00 pm"; a booking with no event yet gets one; a failure of Google is written on the row and never undoes the move). If the booking has proof of how it was made, the move and the cancellation are added to the proof's `changes` list (shown to the owner; the sealed proof itself is not rewritten).

A booking row (`appointments.json`) keeps the doctor-booking keys (`doctor`, `specialty`, `weekday`, `reason`) so the older tools still read the file, and adds `schedule_id`, `schedule_name`, `agent_id`, `start`, `end`, `timezone`, `source` ("staff" or "agent"), `nights`, `party`, `note`, `version`, and after a move `moved_from` and `moved_at`. Whether a time is free is decided inside the store's lock, so two bookings of one slot cannot both succeed. Google Calendar sync, the link to the call and its signed receipt (`conversation_id`, `receipt_id`) work as before; a stay is one event from check-in to check-out.

### Hours from words

`POST /api/schedules/parse-hours {"text"}` → `{week, slot_minutes, closed_dates, notes, confident, source}`. See section 5.

## 4. The agent's tools

The built-in tool server `schedules` (in-process, bound to the front desk on the call). An agent gets its tools when `"schedules"` is in its `tools`, **or whenever it has schedules**; then the older doctor-list `appointments` tools are left out. The templates set `tools: ["schedules"]`.

| Tool | Arguments | Answer |
|---|---|---|
| `check_availability` | `schedule?`, `date`, `time?`, `nights?` | `{schedule, available, reason, date, time, weekday, before, after, alternatives, say {en, hi}}` (`before`/`after` as `{date, weekday, time}`) |
| `list_free_slots` | `schedule?`, `date` | `{schedule, date, weekday, slot_minutes, count, free: ["10:00", …≤16], more?}`; a stay: the rooms left on the coming 7 nights |
| `book` | `schedule?`, `date`, `time?`, `nights?`, `name`, `phone` | `{booking_id, schedule, name, date, weekday, time, status, say}`; refused: the reason and the alternatives |
| `cancel` | `booking_id` | the cancelled booking; only this front desk's own, not yet past. Read back first: the caller's yes to "Shall I cancel booking A12, Thursday 5 November at 3 pm?" |
| `reschedule` | `booking_id`, `date`, `time?`, `nights?`, `schedule?` | the moved booking (same id) with `moved_from {date, weekday, time}` and `say {en, hi}`; refused: the reason and the alternatives. The caller's yes to the NEW day and time read back, as for `book`. Use `find_booking` first when the id is not known; never cancel and book again |
| `whatsapp_consent` | `agreed` | only when the WhatsApp confirmations are in the build (`WHATSAPP.md`): records the caller's answer to "Shall I send the confirmation on WhatsApp?" |
| `find_booking` | `phone` | `{bookings: […]}` (extra to the contract: a caller who wants to cancel or change needs the id) |

`schedule` is matched by name, loosely and in Hindi or English ("doctor", "डॉक्टर", "lab"); with one schedule it may be left out; with several and no clear match the tool answers with the names so the agent asks. Dates and times arrive already worked out from the caller's words by `when.normalise_tool_args`.

**The prompt rule.** Every template's instructions end with it, and the tools section of the system prompt repeats it whenever the schedule tools are present: never say a time is free, open or taken without calling `check_availability` first; when it is not free, offer the `before` and `after` times it returns in one short sentence and ask which the caller prefers; when they choose, read the day, date and time back, ask "Shall I book it?" and call `book` only after a spoken yes; for stays pass the nights. The turn loop enforces the read-back (`book` first answers NOT DONE with the sentence to read) and the same for `reschedule` (the new day and time) and `cancel` (the previous agent turn must name the booking's own day and time, looked up by its id among this front desk's bookings, and ask; the caller's next words must be a plain yes; the gate gives the sentence in English, Hindi and Marathi). A reply that claims a booking while no write tool succeeded is replaced by a safe line. After `book`, `reschedule` and `cancel` the tool result carries `whatsapp` (for example `{"status": "ask"}`) when the WhatsApp confirmations answer; the prompt then tells the agent to ask once and call `whatsapp_consent`.

## 5. Opening hours from words

`hours_parser.parse(text)` reads what an owner types or says. A deterministic parser goes first (microseconds, no model); Gemini Flash is asked **only when the parser understood no hours, no slot length and no closed date**, and its answer goes through `normalise_week` like any week (an invalid answer is thrown away). `source` is `"parser"`, `"model"` or `null`; a model answer always has `confident: false` and a note.

Reads: day names in English, romanised Hindi and Marathi and Devanagari; ranges ("Mon to Sat", "Mon-Fri", "सोमवार से शनिवार", "सोमवार ते शुक्रवार", wrapping "Fri to Mon"); lists ("Mon, Wed and Fri"); "all days / daily / roz / रोज़", "weekdays", "weekends", "except Sunday"; times ("10 to 1", "10am-6pm", "10:30 to 14:00", "9 se 5", "10 बजे से 2 बजे तक", "सुबह 10 से 2", "सकाळी 10 ते 2", "das se do", "open 24 hours", "24x7"); "closed", "off", "बंद", "छुट्टी", "सुट्टी" with the day before or after; breaks ("lunch 1 to 2", "with a 13:30 to 14:30 break", "लंच") that cut the group they follow, or every open day when they come after a closed day; a slot length ("every 15 minutes", "30 min slots", "hourly", "हर 20 मिनट"); closed dates ("closed on 25 December").

Times with no am or pm are read as the shortest range that ends after it starts, with 1 to 7 as afternoon or evening and 8 to 11 as morning ("10 to 1" is 10:00 to 13:00, "5 to 9" is 17:00 to 21:00). When that is a real guess (a start at 6 or 7 that could be the morning) a note says so and `confident` is false. Overlapping ranges are joined and the note says so.

Does not read: ranges across midnight ("10 pm to 2 am"; split it into two days), alternating weeks, "the first Sunday", named holidays, other languages. Words it does not recognise are listed in `notes` ("I did not understand: Diwali.") and `confident` is false. The caller of the API should show the result for review, never save it blind.

Example: `"Mon to Sat 10 to 1 and 5 to 9, Sunday closed"` →

```json
{"week": {"mon": [{"start": "10:00", "end": "13:00"}, {"start": "17:00", "end": "21:00"}], "…": "same to sat", "sun": []},
 "slot_minutes": null, "closed_dates": [], "notes": [], "confident": true, "source": "parser"}
```

## 6. Front-desk templates

`GET /api/agent-templates` lists eight types in this order: `clinic`, `hotel`, `salon`, `restaurant`, `office`, `institute`, `service_centre`, `fitness`. Each has `type_label`, `tagline`, `illustration` (= id), `accent`, `persona {tone, traits[3], sample_line {en, hi}}`, `languages`, `greeting` and the instructions (in the template file) with `{org}` and `{city}` placeholders, `suggested_questions`, `schedules` (the default schedule specs), and `personalise {name_pattern, fallback_org, …}`.

`POST /api/agents/from-template {template_id, org_name?, city?, languages?, details?, include_sample?, voice_id?}` creates the agent (`template_id` stored on it and returned by `GET /api/agents`; `PUT` accepts it to change the type, `null` to clear) and its default schedules bound to it, and returns the agent with `schedules` (each with its `summary`). `{org}` becomes the organisation name (or "our clinic" and so on when none is given) and " in {city}" disappears without a city. The sample document is labelled as a sample.

| Template | Default schedule |
|---|---|
| `clinic` | "Doctor consultations", 15 min, Mon to Sat 10:00 to 13:00 and 17:00 to 20:00, 1 at a time |
| `hotel` | "Rooms", nightly, 10 rooms, check-in 12:00, check-out 11:00, 90 days ahead |
| `salon` | "Appointments", 30 min, Tue to Sun 10:00 to 20:00, 2 at a time |
| `restaurant` | "Table bookings", 30 min starts, every day 12:00 to 15:30 and 19:00 to 23:00, 8 tables |
| `office` | "Consultations", 30 min, Mon to Fri 10:00 to 18:00 with a 13:30 to 14:30 break |
| `institute` | "Campus visits and counselling", 30 min, Mon to Sat 10:00 to 17:00 |
| `service_centre` | "Service slots", 60 min, Mon to Sat 9:00 to 18:00, 3 at a time |
| `fitness` | "Trial sessions", 60 min, every day 6:00 to 10:00 and 17:00 to 21:00, 5 at a time |

### Demo data

`./vakya demo` (needs `./vakya start`) fills an empty desk so the Bookings pages never open empty: a clinic front desk ("Green Valley Clinic") and a hotel front desk ("Palm Grove Residency") made from the templates through `POST /api/agents/from-template`, then about a dozen staff bookings on the clinic's slots across today and the next six days (always inside opening hours, Sundays skipped), two blocked ranges, and five stays of 1 to 3 nights on the hotel's Rooms schedule. Everything is made up (invented names, some in Devanagari, phone numbers from the `+91 98765 00xxx` test block) and every row has source `staff`: no calls, transcripts, proof of booking or caller profiles are created. It only adds: before the first write it copies `backend/data/` to `backend/data-backups/<YYYY-MM-DD-HHMM>/` (ignored by git) and prints that path. Run again, it finds the desks by name and makes nothing new; `./vakya demo --add-bookings` tops up days that have no booking. Code: `backend/scripts/demo_data.py`, tests `backend/tests/test_demo_data.py`.

### Running late and tokens

A time-slot schedule can say it is running late today, and every booking of the day has a token (its place by start time). The phone agent answers "how many before me?" from them. See [RUNNING_LATE.md](RUNNING_LATE.md).
