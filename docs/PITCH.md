# Vakya: one-page pitch

**Call once, it's booked.** A voice front desk on a real phone line that books, moves and cancels appointments in Hindi, Marathi, English or a mix, reads every detail back before it commits, and confirms on WhatsApp and Google Calendar. Built on Gnani Prisma (speech recognition) and Timbre (speech).

## Problem

A small clinic's phone rings while the receptionist is with a patient. The call goes unanswered and the patient books somewhere else. Callers mix languages in one sentence on poor 8 kHz lines, so generic phone bots mishear names, days and digits, and a wrong booking is worse than none.

## Solution

- **Answers the call** on a Vobiz number, says it is an AI, and talks in the caller's language.
- **Checks the real schedule**: if a time is taken it offers the nearest free ones.
- **Reads back before committing**: day, time, name and phone number; the caller corrects anything, then says yes.
- **Books, moves or cancels** in the same call, then sends a WhatsApp confirmation and updates Google Calendar.
- **Remembers callers**: a repeat caller is greeted by name with their upcoming booking.
- **Owner's desk**: every call, transcript, booking and message in one place, plus a signed receipt for each booking.

## Who pays

Independent clinics and dental practices first, then salons, small hotels, service centres and coaching institutes: places where one missed call is a lost booking and nobody is paid to sit at the phone.

## Proof

| Measure | Result |
| --- | --- |
| Booking fields heard, 24 invented calls on Gnani Prisma, clean to 5 dB noise | 100% |
| Same, noise at 0 dB | 96% (100% with keyterms) |
| Live call, 9 Nov (in the demo video) | booked, booked again and cancelled in Hindi and English; WhatsApp and Calendar updated each time |
| Automated tests | 3,100+ backend, 600+ frontend pass |

## Why Gnani

Prisma handles code-mixed Indian speech at phone quality and held 100% on name, day and time down to 5 dB noise in our test. Timbre speaks at 8 kHz natively, so the line gets audio in its own format.

## What's next

Cut reply start time on calls to under 2 s, test inbound calls live, add booking safety rules (confirmation expiry, cancel notice, limits per number), and pilot with two or three clinics.

**Try it:** vakya.ayushchougula.in · demo video at vakya.ayushchougula.in/demo
