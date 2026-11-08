"use client";

import { CalendarCheck, FileText, History, Languages, MessageCircle, PhoneCall, ShieldCheck, UserRoundCheck, LayoutGrid } from "lucide-react";
import type { ComponentType } from "react";
import { Item, Reveal, SplitHeading, Stagger } from "./motion";

export const FEATURES: { icon: ComponentType<{ className?: string }>; title: string; body: string }[] = [
  { icon: PhoneCall, title: "A real phone number", body: "Answers a real phone number, on 8 kHz phone lines, not a browser demo." },
  { icon: ShieldCheck, title: "Read back, then yes", body: "Books, moves and cancels during the call, after reading every detail back and hearing a yes." },
  { icon: MessageCircle, title: "WhatsApp confirmation", body: "A WhatsApp confirmation is sent before the caller hangs up." },
  { icon: CalendarCheck, title: "Google Calendar, live", body: "Google Calendar is updated live as bookings change." },
  { icon: UserRoundCheck, title: "Remembers callers", body: "Remembers returning callers by number: their bookings, moves and cancellations." },
  { icon: Languages, title: "Hindi, Marathi, English", body: "Understands Hindi, Marathi, English and code-mixed speech." },
  { icon: FileText, title: "Answers from your documents", body: "Upload your FAQ, price list or policy (PDF, TXT, Markdown). FAQ question-answer pairs are kept together, search understands Hindi and Hinglish spellings, and questions it could not answer are listed for you to fill in." },
  { icon: LayoutGrid, title: "Visual schedules", body: "Set opening hours and slots by sight, not forms." },
  { icon: History, title: "Callback, live view, audit", body: "Missed-call callback, a live call view with replay, and a booking audit trail." },
];

export function WhatVakyaDoes() {
  return (
    <section id="features" aria-labelledby="features-title" className="s-container mt-24 md:mt-32">
      <div className="flex flex-col items-center text-center">
        <SplitHeading id="features-title" text="What Vakya" accent="does." className="h2 max-w-[14ch]" />
        <Reveal as="p" className="lede mt-5 max-w-[52ch]">
          One front desk that answers the phone, checks the schedule and confirms what it booked.
        </Reveal>
      </div>
      <Stagger as="ul" aria-label="Features" className="mt-10 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {FEATURES.map((f) => (
          <Item as="li" key={f.title} className="min-w-0">
            <div className="h-full rounded-2xl border border-[var(--s-line)] bg-[var(--s-paper)] p-5" style={{ boxShadow: "var(--s-shadow-paper)" }}>
              <f.icon className="h-5 w-5 text-[var(--violet-700)]" aria-hidden />
              <h3 className="s-item mt-3">{f.title}</h3>
              <p className="mt-1 text-sm leading-relaxed text-[var(--s-mute)]">{f.body}</p>
            </div>
          </Item>
        ))}
      </Stagger>
      <DocsToAnswer />
    </section>
  );
}

/** Illustrative only: sample text showing the path from a document to a spoken answer. */
export function DocsToAnswer() {
  return (
    <Reveal as="figure" className="m-0 mt-8" aria-label="Your documents, its answers (illustration)">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3" data-testid="docs-to-answer">
        <Step label="1. From your FAQ">
          <p className="font-semibold">Q: Sunday ko clinic khula hai?</p>
          <p className="mt-1">A: Sunday ko clinic band rehta hai. Monday se Saturday, subah 9 se shaam 5 baje tak.</p>
        </Step>
        <Step label="2. Caller asks, in Hinglish">
          <p lang="hi-Latn">&ldquo;Sunday ko doctor milenge kya?&rdquo;</p>
        </Step>
        <Step label="3. Spoken answer">
          <p lang="hi-Latn">&ldquo;Sunday ko clinic band rehta hai. Aap Monday se Saturday, subah 9 se shaam 5 baje ke beech aa sakte hain.&rdquo;</p>
        </Step>
      </div>
      <figcaption className="mt-3 text-center text-sm text-[var(--s-mute)]">Sample text, for illustration. Questions it cannot answer are listed so you can fill them in.</figcaption>
    </Reveal>
  );
}

function Step({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-[var(--s-line)] bg-[var(--s-fill)] p-5 text-sm leading-relaxed text-[var(--s-ink)]">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--s-mute)]">{label}</p>
      {children}
    </div>
  );
}
