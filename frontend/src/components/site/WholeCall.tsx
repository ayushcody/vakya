"use client";

import { CalendarCheck, CircleCheck, CircleX, Search } from "lucide-react";
import type { ReactNode } from "react";
import { Reveal, SplitHeading } from "./motion";
import { WhatsAppMark } from "./PartnerLogos";

type Line =
  | { who: "agent" | "caller"; hi: string; en: string }
  | { event: "check" | "booked" | "cancelled"; text: string; whatsapp?: boolean };

/** The real call in the demo video (9 Nov 2026, Green Valley Clinic, a member of our team calling), word for word as
 *  Vakya transcribed it, with an English line under each. Only the phone number is shortened. */
export const CALL: Line[] = [
  { who: "agent", hi: "नमस्ते Ayush जी, फिर से आपका स्वागत है। आज कैसे मदद करूँ?", en: "Hello Ayush, welcome back. How can I help today?" },
  { who: "caller", hi: "मुझे बुधवार शाम छह बजे बुकिंग करनी थी।", en: "I wanted a booking on Wednesday at 6 in the evening." },
  { event: "check", text: "Checks Wednesday 6 PM: taken. Finds the nearest free times." },
  { who: "agent", hi: "बुधवार 11 नवंबर शाम 6 बजे का समय भरा हुआ है। क्या आप शाम 5 बजे या शाम 7 बजे चुनना चाहेंगे?", en: "Wednesday 11 November at 6 PM is taken. Would you like 5 PM or 7 PM?" },
  { who: "caller", hi: "हाँ, चलेगा। मुझे सात बजे कर दीजिए booking।", en: "Okay. Book it for 7, please." },
  { who: "agent", hi: "बुधवार 11 नवंबर शाम 7 बजे, Ayush के नाम से, फ़ोन नंबर के आख़िरी अंक 6 2 9 5. क्या मैं बुक कर दूँ?", en: "Wednesday 11 November, 7 PM, for Ayush, number ending 6 2 9 5. Shall I book it?" },
  { who: "caller", hi: "हाँ, कर दीजिए।", en: "Yes, go ahead." },
  { event: "booked", text: "Booked A107, Wednesday 7 PM. Added to Google Calendar.", whatsapp: true },
  { who: "agent", hi: "बुकिंग हो गई है। बुकिंग आईडी A107 है, बुधवार 11 नवंबर, शाम 7 बजे। आपका टोकन नंबर 11 है। पुष्टि का संदेश WhatsApp पर भेजा जाएगा।", en: "Booked. Your booking ID is A107, Wednesday 11 November, 7 PM. Your token number is 11. The confirmation is on WhatsApp." },
  { who: "caller", hi: "Alright, you know what… गुरुवार को ग्यारह बजे करना है।", en: "Alright, you know what… I want Thursday at 11." },
  { who: "agent", hi: "गुरुवार 12 नवंबर सुबह 11 बजे, Ayush के नाम से, फ़ोन नंबर के आख़िरी अंक 6 2 9 5. क्या मैं बुक कर दूँ?", en: "Thursday 12 November, 11 AM, for Ayush, number ending 6 2 9 5. Shall I book it?" },
  { who: "caller", hi: "हाँ, चलेगा।", en: "Yes, fine." },
  { event: "booked", text: "Booked A108, Thursday 11 AM. Added to Google Calendar.", whatsapp: true },
  { who: "agent", hi: "बुकिंग हो गई है। बुकिंग आईडी A108 है, गुरुवार 12 नवंबर, सुबह 11 बजे। आपका टोकन नंबर 1 है।", en: "Booked. Booking ID A108, Thursday 12 November, 11 AM. Your token number is 1." },
  { who: "caller", hi: "You know what, मुझे वो वाली booking cancel करनी है।", en: "You know what, I want to cancel that booking." },
  { who: "agent", hi: "कौन सी बुकिंग रद्द करनी है? बुधवार 11 नवंबर शाम 7 बजे (A107) या गुरुवार 12 नवंबर सुबह 11 बजे (A108)?", en: "Which booking should I cancel? Wednesday 7 PM (A107) or Thursday 11 AM (A108)?" },
  { who: "caller", hi: "A one zero seven booking cancel करना है मुझे।", en: "I want to cancel A107." },
  { who: "agent", hi: "आपकी बुकिंग A107 है, बुधवार 11 नवंबर शाम 7 बजे। क्या मैं A107 रद्द कर दूँ?", en: "Your booking A107 is Wednesday 11 November at 7 PM. Shall I cancel A107?" },
  { who: "caller", hi: "हाँ, चलेगा, कर दीजिए।", en: "Yes, do it." },
  { event: "cancelled", text: "Cancelled A107. Removed from Google Calendar.", whatsapp: true },
  { who: "agent", hi: "बुकिंग A107, बुधवार 11 नवंबर शाम 7 बजे, रद्द कर दी गई है। पुष्टि का संदेश WhatsApp पर भेजा जाएगा।", en: "Booking A107, Wednesday 11 November at 7 PM, has been cancelled. The confirmation is on WhatsApp." },
  { who: "caller", hi: "Alright, thank you. धन्यवाद। Bye.", en: "Alright, thank you. Bye." },
];

const EVENT_ICON: Record<"check" | "booked" | "cancelled", ReactNode> = {
  check: <Search className="size-4" aria-hidden />,
  booked: <CircleCheck className="size-4" aria-hidden />,
  cancelled: <CircleX className="size-4" aria-hidden />,
};

export function WholeCall() {
  return (
    <section id="the-call" aria-labelledby="the-call-title" className="s-container mt-24 md:mt-32">
      <div className="flex flex-col items-center text-center">
        <SplitHeading id="the-call-title" text="Read the" accent="whole call." className="h2 max-w-[16ch]" />
        <Reveal as="p" className="lede mt-5 max-w-[56ch]">
          The real call from the demo, word for word as Vakya heard it: one booking, a second one, and a cancellation, with
          WhatsApp and Google Calendar updated at each step.
        </Reveal>
      </div>
      <Reveal as="div" className="mx-auto mt-10 max-w-3xl">
        <ol aria-label="The call, line by line" className="space-y-3">
          {CALL.map((l, i) =>
            "event" in l ? (
              <li key={i} className="flex flex-wrap items-center justify-center gap-2 py-1 text-xs text-[var(--s-mute)]">
                <span className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 ${l.event === "cancelled" ? "border-rose-200 bg-rose-50 text-rose-700" : l.event === "booked" ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-[var(--s-line)] bg-[var(--s-fill)]"}`}>
                  {EVENT_ICON[l.event]}
                  {l.text}
                </span>
                {l.whatsapp && (
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-[#25D366]/40 bg-[#25D366]/10 px-3 py-1 text-[#128C4B]">
                    <WhatsAppMark className="size-4" /> WhatsApp sent
                  </span>
                )}
                {l.event !== "check" && <CalendarCheck className="size-4 text-[#4285F4]" aria-label="Google Calendar updated" />}
              </li>
            ) : (
              <li key={i} className={`flex ${l.who === "caller" ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[85%] rounded-2xl px-4 py-3 ${l.who === "caller" ? "rounded-br-md bg-[var(--violet-700,#6D5BD0)] text-white" : "rounded-bl-md border border-[var(--s-line)] bg-[var(--s-paper)] text-[var(--s-ink)]"}`}>
                  <p className={`mb-1 text-[11px] font-semibold uppercase tracking-wide ${l.who === "caller" ? "text-white/70" : "text-[var(--s-mute)]"}`}>
                    {l.who === "caller" ? "Caller" : "Vakya"}
                  </p>
                  <p lang="hi" className="text-[15px] leading-relaxed">{l.hi}</p>
                  <p className={`mt-1 text-[13px] leading-relaxed ${l.who === "caller" ? "text-white/75" : "text-[var(--s-mute)]"}`}>{l.en}</p>
                </div>
              </li>
            ),
          )}
        </ol>
        <p className="mt-6 text-center text-xs text-[var(--s-mute)]">
          Recorded on a real phone line, 9 November 2026. The clinic and its other patients are sample data; the caller is a
          member of our team. WhatsApp messages were sent from a test number.
        </p>
      </Reveal>
    </section>
  );
}
