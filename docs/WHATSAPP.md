# WhatsApp confirmations

After a call in which a booking was made, moved or cancelled, the caller gets **one** WhatsApp message a short while after hanging up, showing the final state. Book 3 pm: "Booked ... 3:00 pm". Next call, move to 4 pm: "Moved ... from ... 3:00 pm to ... 4:00 pm". Next call, cancel: "Cancelled". The code is `backend/services/booking_notify.py` (outbox, consent, timing), `backend/services/whatsapp.py` (Meta's WhatsApp Cloud API client) and `backend/routers/whatsapp.py` (the Settings page's data).

WhatsApp is the only messaging provider here. Speech is Gnani and the language model is hosted, as everywhere else in Vakya.

## What happens

1. On a call the agent books, moves or cancels. The tool calls `booking_changed()`; nothing is sent yet.
2. The first time a number's booking changes, the agent asks "Shall I send the confirmation on WhatsApp?" and records the answer (`whatsapp_consent`). The answer is stored per number and never asked again.
3. When the call ends (the Vobiz hang-up callback or the audio stream closing, whichever comes first; for a web call, the visitor's hang-up) the message goes `WHATSAPP_SEND_DELAY_S` seconds later (default 30). If a call never reports its end, it goes `WHATSAPP_BACKSTOP_S` seconds after the last change (default 300). A change made by staff on the desk goes after the same short delay, with no call, and only if the caller already agreed.
4. Several changes to one booking on one call become one message with the final state. Book then move: one "Booked" at the new time. Book then cancel: nothing (the caller never held a confirmation; if a message about that booking had already gone out, one "Cancelled"). Move then move: one "Moved" from the original time to the last. Move then cancel: "Cancelled" for the original time.
5. Every message has an idempotency key `booking id : version : kind`. A duplicate hang-up, a retried tool call or a restart cannot send the same message twice. A message that failed is recorded as failed and is not retried automatically.

The outbox lives in `backend/data/whatsapp_outbox.json` (`VAKYA_WHATSAPP_FILE`): pending changes, consent per number (agreed or declined, time, conversation id), used keys, and the last 200 messages. A restart re-schedules whatever was still pending.

The message language is the call's language (English, Hindi or Marathi; the conversation's remembered reply language, then the caller's saved preference), else English.

## Consent

Meta requires the person to have opted in. The agent asks once per number, in the caller's language. "No" is remembered too: nothing is queued and the agent is told "off" on later calls. A message is sent only if consent is "agreed" at the moment of sending. A skipped message is logged with its reason ("no consent").

## Preview mode (until Meta approves the templates)

Without `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` the exact message is still built and stored, labelled **preview** in Settings and in `GET /api/whatsapp/messages`. A preview was **not sent** and is never shown as sent. While in preview the agent still asks for consent, so on a live line turn it off (`WHATSAPP_PREVIEW=off`, or `WHATSAPP_CONFIRMATIONS=off`) so callers are not told something that will not arrive.

## Setup

1. Create a Meta developer account (developers.facebook.com) and a **Business** app. Add the **WhatsApp** product.
2. Use the test phone number Meta provides while you try it. Add the recipients' own numbers as **verified recipients** in the app's WhatsApp API setup; a test number can message only those.
3. Create a **system user** in the Business settings, give it the app and the WhatsApp account, and generate a **permanent access token** with the `whatsapp_business_messaging` and `whatsapp_business_management` permissions. Copy the **phone number id** from the API setup page (it is not the phone number).
4. Create the three templates below in WhatsApp Manager, category **Utility**, in English, Hindi and Marathi (language codes `en`, `hi`, `mr`). Wait for approval.
5. Put these in `backend/.env` and restart:

```
WHATSAPP_ACCESS_TOKEN=...            # never commit, never shown on Settings
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_API_VERSION=v27.0           # optional
WHATSAPP_TEMPLATE_CONFIRMED=vakya_booking_confirmed   # optional names
WHATSAPP_TEMPLATE_MOVED=vakya_booking_moved
WHATSAPP_TEMPLATE_CANCELLED=vakya_booking_cancelled
WHATSAPP_SEND_DELAY_S=30
WHATSAPP_BACKSTOP_S=300
WHATSAPP_PREVIEW=on                  # off: do not even build previews when not set up
WHATSAPP_CONFIRMATIONS=on            # off: never ask, never send
```

A message the business starts (a confirmation after the call, outside any window the customer opened by writing first) must use an **approved template**; free text is not allowed there, which is why only templates are sent.

Settings, "WhatsApp confirmations", shows what is missing (never the token), the three template names and the recent messages with their state: sent, preview, failed or skipped.

## Template texts

Paste these exactly into the template manager. The body uses positional parameters; give a sample for each when Meta asks (for example: Smile Dental, Ravi, Fri 6 Nov, 3:00 pm, A12).

Parameters for `vakya_booking_confirmed` and `vakya_booking_cancelled`: 1 the front desk's name, 2 the person ("Ravi" or "your mother, Sunita"), 3 the time, 4 the booking id. For `vakya_booking_moved`: 1 front desk, 2 person, 3 the old time, 4 the new time, 5 the booking id.

A family booking goes to the number on the booking and names the person: "Hello! Your booking at Smile Dental for your mother, Sunita is confirmed: Fri 6 Nov, 3:00 pm. Booking ID: A12. To change or cancel it, just call us again."

### vakya_booking_confirmed

- en: `Hello! Your booking at {{1}} for {{2}} is confirmed: {{3}}. Booking ID: {{4}}. To change or cancel it, just call us again.`
- hi: `नमस्ते! {{1}} में {{2}} की बुकिंग पक्की हो गई है: {{3}}। बुकिंग आईडी: {{4}}। बदलने या रद्द करने के लिए हमें दोबारा कॉल करें।`
- mr: `नमस्कार! {{1}} येथे {{2}} यांचे बुकिंग निश्चित झाले आहे: {{3}}. बुकिंग आयडी: {{4}}. बदल किंवा रद्द करण्यासाठी आम्हाला पुन्हा कॉल करा.`

### vakya_booking_moved

- en: `Hello! Your booking at {{1}} for {{2}} has been moved from {{3}} to {{4}}. Booking ID: {{5}}. To change it again, just call us.`
- hi: `नमस्ते! {{1}} में {{2}} की बुकिंग {{3}} से बदलकर {{4}} कर दी गई है। बुकिंग आईडी: {{5}}। फिर से बदलने के लिए हमें कॉल करें।`
- mr: `नमस्कार! {{1}} येथे {{2}} यांचे बुकिंग आता {{3}} ऐवजी {{4}} आहे. बुकिंग आयडी: {{5}}. पुन्हा बदलण्यासाठी आम्हाला कॉल करा.`

### vakya_booking_cancelled

- en: `Hello! Your booking at {{1}} for {{2}} on {{3}} has been cancelled. Booking ID: {{4}}. To book again, just call us.`
- hi: `नमस्ते! {{1}} में {{2}} की {{3}} वाली बुकिंग रद्द कर दी गई है। बुकिंग आईडी: {{4}}। दोबारा बुक करने के लिए हमें कॉल करें।`
- mr: `नमस्कार! {{1}} येथे {{2}} यांचे {{3}} असलेले बुकिंग रद्द झाले आहे. बुकिंग आयडी: {{4}}. पुन्हा बुक करण्यासाठी आम्हाला कॉल करा.`

(A test keeps these identical to the texts the code builds.)

## What is not verified

- The request shape (endpoint, `template.components[].body.parameters`) follows Meta's template-message guide. The pages read while writing this (messages endpoint and version v27.0, error shape, template placeholders) are under developers.facebook.com/documentation/business-messaging/whatsapp/ (`messages/send-messages.md`, `direct-send/send-utility-and-authentication-messages.md`, `templates/components.md`); none printed a full template send example, so the first real send is the check. No real message has been sent from Vakya.
- Meta's rules on template approval, categories, pricing and rate limits are not stated here; read them in Meta's documentation before relying on them.
- On 8 Nov 2026 the approved templates (confirmed, moved, cancelled) in English, Hindi and Marathi were delivered live.
- Hindi and Marathi template wording has not been reviewed by a native speaker.
- A web call whose booking row has no conversation id is held to the backstop time.
