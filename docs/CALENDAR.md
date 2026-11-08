# Google Calendar

Vakya can keep one Google Calendar in step with its bookings, in both directions.

* **Out.** A booking made on a call or at the desk becomes an event; moving the booking moves the event (same event, new start and end, and the description says "Moved from Thu 5 Nov 3:00 pm"); cancelling the booking deletes it. A stay is one event from check-in to check-out.
* **In.** The owner's own events block times. A lunch, a holiday or a meeting on the same calendar makes those slots unavailable to callers and to staff bookings, with the reason "busy on the calendar" ("We are busy Thursday at 4 pm. 5 pm or 6 pm, which would suit you?"). Only time-slot schedules read the calendar; a stay (rooms by the night) does not.

Without the two settings below nothing here runs and the desk works from its own list exactly as before. Code: `backend/services/gcal.py` (the client), `backend/mcp_servers/appointments.py` (`sync_booking`, `sync_move`, `sync_cancellation`), `backend/services/availability.py` (the Engine takes the busy times), tests `backend/tests/test_calendar_read.py`, `test_reschedule.py`, `test_appointments_calendar.py` (all with a fake Google; nothing in the suite talks to Google). On 8 Nov 2026 a real calendar event was created and deleted live from a booking.

## Switching it on

1. In the Google Cloud console create a project (or pick one) and **enable the Google Calendar API** for it.
2. Create a **service account** (IAM and admin, Service accounts) and add a **JSON key** to it. Download the file.
3. Save the file as `backend/data/google_calendar.json`. It is a secret: keep it out of git. Another path can be given with `GOOGLE_SERVICE_ACCOUNT_FILE`.
4. In Google Calendar open the calendar's settings, **Share with specific people**, add the service account's e-mail address (`...@....iam.gserviceaccount.com`) with the permission **Make changes to events**.
5. Copy the calendar's id (Settings, Integrate calendar, Calendar ID) into `backend/.env`:

```
GOOGLE_CALENDAR_ID=abc123@group.calendar.google.com
```

Restart `./vakya start`. Until both settings exist every booking says `calendar_sync: "not_configured"` in `GET /api/bookings`; afterwards `synced`, `pending` or `failed`. One calendar serves every schedule for now. Times use `VAKYA_TIMEZONE` (default `Asia/Kolkata`) unless a schedule has its own timezone.

## What goes to Google, and what comes back

| Moment | Google call |
|---|---|
| a booking is saved | `events.insert` (title "Appointment: name ...", booking id, phone, reason; the event carries a private `vakyaRequest` property with the booking id) |
| a booking is moved | `events.patch` of the same event: start, end, description |
| a booking is cancelled | `events.delete` |
| a time is checked | `events.list` for the day (`singleEvents=true`, `orderBy=startTime`), at most two seconds |

Events read back are skipped when they are cancelled, shown as available (transparent, "Show as available"), declined by the calendar, or one of Vakya's own (they have the `vakyaRequest` property: they are bookings already and the booking list counts them). An **all-day** event blocks its whole days only when it is shown as busy; an all-day event shown as available (Google's default for them) blocks nothing. An event that runs past midnight is cut at the days.

## The patient gets no Google invite

Vakya does not add the caller as a guest. A service account cannot invite guests on an ordinary Google account (Google refuses it unless the calendar is on a Workspace domain with delegation, which a small clinic does not have). So the caller's confirmation goes by **WhatsApp** (`docs/WHATSAPP.md`), not by a calendar invitation. The calendar is the owner's view of the day.

## When Google is slow or down

The calendar never breaks a booking and never fails a caller's request.

* **Writing.** The booking, the move or the cancellation is saved first. The event is sent after that, and the call waits at most 2.5 seconds for Google (the desk waits longer); a slower call finishes in the background. A failure is logged and written on the booking as `calendar_sync: "failed"` with `calendar_error`; the desk's booking list shows it. A move whose event the owner deleted by hand makes a new event; a booking that never reached the calendar gets its event when it is moved.
* **Reading.** If the events cannot be read within about two seconds (or Google answers an error), Vakya uses its own list for that request, writes a warning to the log, and remembers the failure for 15 seconds so the next callers do not each wait for the timeout. Callers hear nothing different.
* **Remembering.** What was read for a day is kept for **60 seconds** per calendar and day (one read covers 14 days, so looking for the nearest free time costs a few requests, not one per day). A booking, move or cancellation made through Vakya forgets its day at once. An event the owner adds in Google therefore blocks a time within a minute, not instantly: between the owner's edit and the next read a caller could still be offered that time.
* A booking is read against the calendar before the appointments file is locked, so a slow Google never holds up other bookings.

## What is not covered

Party size and capacity above one: an owner event blocks the whole slot of the schedule, however many bookings it could hold. A different calendar per schedule, a two-way sync of events the owner moves by hand (a booking's event moved in Google does not move the booking), and guests on events are not built.
