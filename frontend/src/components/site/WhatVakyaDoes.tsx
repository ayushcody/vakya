"use client";

import { Brain, FileText, Headphones, History, Languages, LayoutGrid, ReceiptText } from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { Item, Reveal, SplitHeading, Stagger } from "./motion";
import { GnaniMark, GoogleCalendarMark, VobizMark, WhatsAppMark } from "./PartnerLogos";

/** The four things that set Vakya apart, each with the service it works through. */
export const USPS: { id: string; title: string; body: ReactNode; mark: ReactNode; note?: string }[] = [
  {
    id: "phone",
    title: "A real phone call, not a browser demo",
    body: "Callers dial a real number over Vobiz telephony (8 kHz phone audio). Gnani Prisma hears them live, Gnani Timbre answers in Hindi, Marathi or English.",
    mark: <span className="flex flex-wrap items-center gap-2"><VobizMark /><GnaniMark /></span>,
  },
  {
    id: "memory",
    title: "Thinks before it books, and remembers you",
    body: "It checks real availability, offers the nearest free slots and reads every detail back. It only books on a clear yes. Reschedule or cancel in the same call, and returning callers are greeted by name: “Welcome back, Ayush.”",
    mark: <span className="grid size-9 place-items-center rounded-full bg-[color-mix(in_srgb,var(--violet-700,#6D5BD0)_14%,white)]"><Brain className="size-5 text-[var(--violet-700)]" aria-hidden /></span>,
  },
  {
    id: "whatsapp",
    title: "WhatsApp, the moment it acts",
    body: "The confirmation lands the moment it books, moves or cancels, while the caller is still on the line.",
    mark: <WhatsAppMark />,
    note: "This demo sends from a WhatsApp test number.",
  },
  {
    id: "calendar",
    title: "Straight into your Google Calendar",
    body: "Every booking, move and cancellation lands in the owner's own Google Calendar, so they know the moment someone books.",
    mark: <GoogleCalendarMark />,
  },
];

export const FEATURES: { icon: ComponentType<{ className?: string }>; title: string; body: string }[] = [
  { icon: Headphones, title: "Watch live, take over", body: "The owner can watch the live transcript of any call and take it over at any moment." },
  { icon: ReceiptText, title: "A receipt for every booking", body: "Every booking gets a verifiable receipt: who called, what was said and how it was booked." },
  { icon: Languages, title: "Hindi, Marathi, English", body: "Understands Hindi, Marathi, English and code-mixed speech." },
  { icon: FileText, title: "Answers from your documents", body: "Upload your FAQ, price list or policy (PDF, TXT, Markdown). Search understands Hindi and Hinglish spellings, and questions it could not answer are listed for you to fill in." },
  { icon: LayoutGrid, title: "Visual schedules", body: "Set opening hours and slots by sight, not forms." },
  { icon: History, title: "Callback and audit", body: "Missed-call callback, call replay and a booking audit trail." },
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
      <Stagger as="ul" aria-label="What sets Vakya apart" className="mt-10 grid grid-cols-1 gap-3 md:grid-cols-2">
        {USPS.map((u) => (
          <Item as="li" key={u.id} className="min-w-0">
            <div className="flex h-full flex-col rounded-3xl border border-[var(--s-line)] bg-[var(--s-paper)] p-6 md:p-7" style={{ boxShadow: "var(--s-shadow-paper)" }} data-usp={u.id}>
              <div className="min-h-9">{u.mark}</div>
              <h3 className="mt-5 font-[family-name:var(--s-font-display)] text-[1.65rem] leading-tight tracking-tight text-[var(--s-ink)]">{u.title}</h3>
              <p className="mt-2 text-[15px] leading-relaxed text-[var(--s-mute)]">{u.body}</p>
              {u.note && <p className="mt-auto pt-4 text-xs text-[var(--s-mute)]">{u.note}</p>}
            </div>
          </Item>
        ))}
      </Stagger>
      <Stagger as="ul" aria-label="Features" className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
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
