# Vakya: one-page pitch

## Problem

A small clinic's phone rings while the receptionist is with a patient. The call goes unanswered, and that patient books somewhere else. Callers speak Hindi, Marathi, English or a mix in one sentence, on poor 8 kHz lines, so a generic phone bot mishears names and digits.

## Who pays

Independent clinics and dental practices first, then salons, small hotels, service centres and coaching institutes: places where one missed call is a lost booking and nobody is paid to sit at the phone. The buyer is the owner, who sees bookings made and calls answered on one desk.

## Demo flow (about 90 seconds)

1. A call to the clinic's Vobiz number. Vakya says it is an AI and asks in Hindi what the caller needs.
2. The caller asks for an appointment. Vakya checks the schedule, offers a free time, and reads back the day, time and phone number.
3. The caller corrects one digit. Vakya reads it again. The caller says yes and the booking is made (A28).
4. A second call moves it from 5 to 6 PM. A third cancels A27. Each needs one yes.
5. The owner's desk shows the booking, the Google Calendar event, and the WhatsApp confirmation (approved template, Hindi) arriving on the phone.
6. Calling again, the caller is greeted by name with their upcoming appointment.

## Proof

| Measure | Result |
| --- | --- |
| Booking fields heard, 24 invented calls on Gnani Prisma, clean to 5 dB noise | 100% |
| Same, noise at 0 dB | 96% (100% with keyterms) |
| Ten-digit phone numbers heard whole at 5 dB | 3 of 6 (hence the read-back) |
| Speech recognition time | median 0.8 s per clip |
| Live calls, 8 Nov | booked, moved and cancelled in Hindi on the first yes; calendar and WhatsApp confirmed |
| Automated tests | 3152 backend, 632 frontend pass |

Misses: reply start on a live call is about 3.7 to 6 s today (first filler sound at about 1.1 s), and inbound calling is built but not yet tested live. The noise audio is synthetic.

## Why Gnani

Prisma handles Hindi, Marathi and English mixed in one sentence at phone quality, and held 100% on name, day and time down to 5 dB noise in our test. Timbre speaks at 8 kHz natively, so the line gets audio in its own format. Both are Indian-language products, which is the market this serves.

## What's next

- Cut reply start time on calls to under 2 s.
- Test inbound calls on a live number and finish the missed-call callback.
- Add the booking safety rules: 3-minute confirmation expiry, 60-minute cancel notice, 3 active bookings per number, no double booking, an audit log.
- Repeat the noise test on real recordings, with consent.
- Pilot with two or three clinics.
