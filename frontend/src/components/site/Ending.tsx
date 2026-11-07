"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowUpRight } from "lucide-react";
import { Logo } from "@/components/Logo";
import { REPO_PUBLIC, REPO_URL } from "./repo";
import { ALL_SITE_PAGES } from "./routes";
import { Magnetic, Reveal, SplitHeading } from "./motion";

/**
 * The end of the landing page: Faq, FinalCta and Footer.
 * Everything is readable and operable without JS: the FAQ is native <details> and the CTA panel is static markup.
 */

const REPO = REPO_URL;
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8020";

/* ------------------------------------------------------------------ styles (scoped, no global edits) */

const ENDING_CSS = `
/* FAQ: native details; the height animates where ::details-content is supported, and simply snaps where it is not. */
.site .faq-item { interpolate-size: allow-keywords; }
.site .faq-item > summary { list-style: none; }
.site .faq-item > summary::-webkit-details-marker { display: none; }
.site .faq-item .faq-bar-v { transition: transform .3s cubic-bezier(.16,1,.3,1); transform-origin: center; }
.site .faq-item[open] .faq-bar-v { transform: scaleY(0); }
@supports selector(::details-content) {
  .site .faq-item::details-content {
    block-size: 0;
    overflow: hidden;
    transition: block-size .38s cubic-bezier(.16,1,.3,1), content-visibility .38s allow-discrete;
  }
  .site .faq-item[open]::details-content { block-size: auto; }
}
@media (prefers-reduced-motion: reduce) {
  .site .faq-item::details-content { transition: none !important; }
  .site .faq-item .faq-bar-v { transition: none; }
}

`;

/* ------------------------------------------------------------------ FAQ */

type Item = { q: string; a: ReactNode; text: string };

const link = "underline underline-offset-4 decoration-[var(--s-bar)] hover:decoration-[var(--s-ink)]";

const FAQ_ITEMS: Item[] = [
  {
    q: "Has anyone used it?",
    text: "No. Vakya has had no customers, no clinics and no real callers. Every booking, name and phone number on this site is invented, and the test audio is synthetic speech. We say so because the honest answer is more useful to a judge than a vague one.",
    a: (
      <>
        <p>
          No. Vakya has had no customers, no clinics and no real callers. Every booking, name and phone number on this site is invented,
          and the test audio is synthetic speech.
        </p>
        <p>Nothing here is a claim about real clinics. The report card is still empty because Gnani Prisma has not been run yet.</p>
      </>
    ),
  },
  {
    q: "What does it cost?",
    text: `Nothing from Vakya: there is no subscription and no per-minute fee. ${REPO_PUBLIC ? "The source is on GitHub and you run it on your own computer." : "The code is MIT-licensed and shared on request, and you run it on your own computer."} Speech from Gnani (Prisma and Timbre) is billed by Gnani against your own API key. A Embeddings key writes the receptionist's replies (Google sets its own limits and prices). Phone minutes are billed by your telephony provider, Vobiz.`,
    a: (
      <>
        <p>
          Nothing from Vakya. There is no subscription and no per-minute fee:{" "}
          {REPO_PUBLIC ? (
            <>
              the source is on{" "}
              <a className={link} href={REPO}>
                GitHub
              </a>{" "}
              and you run it on your own computer.
            </>
          ) : (
            "you run it on your own computer. The code is MIT-licensed and shared on request."
          )}
        </p>
        <p>Costs that can appear, and who charges them:</p>
        <ul>
          <li>Speech from Gnani (Prisma to listen, Timbre to speak) is billed by Gnani against your own API key.</li>
          <li>A Embeddings key writes the front desk&apos;s replies and searches its documents. Google sets its own limits and prices.</li>
          <li>Phone minutes are billed by your telephony provider (Vobiz), not by Vakya.</li>
        </ul>
      </>
    ),
  },
  {
    q: "Which services does it use?",
    text: "Gnani Prisma recognises what the caller says and Gnani Timbre speaks, both through Gnani's API with your key. Embeddings Flash (Google, hosted) decides what to say, and Embeddings embeddings search the business's documents, with keyword search as the backup. Vobiz carries the phone call. There is no second speech model: when Gnani is busy or down, the caller hears a short pre-rendered line and is offered a callback. The Gnani code has been tested against fakes only until a live key proves it.",
    a: (
      <>
        <ul>
          <li>Gnani Prisma recognises what the caller says, and Gnani Timbre speaks, both through Gnani&apos;s API with your key.</li>
          <li>Embeddings Flash (Google, hosted) decides what to say, and Embeddings embeddings search the business&apos;s documents, with keyword search as the backup. Gnani does the speech, not the reasoning.</li>
          <li>Vobiz carries the phone call.</li>
          <li>There is no second speech model. When Gnani is busy or down, the caller hears a short pre-rendered line and is offered a callback.</li>
        </ul>
        <p>First live contact with Gnani was on 5 Nov: Prisma and Timbre work. The realtime stream, cloning and a real phone call have not run yet.</p>
      </>
    ),
  },
  {
    q: "Which languages does it handle?",
    text: "Hindi, Marathi and English, and callers who mix them in one sentence. Gnani's Prisma and Timbre cover more Indian languages, but Vakya has only been tested in these three. The front desk answers in the language the caller starts in and follows them if they switch.",
    a: (
      <>
        <ul>
          <li>Hindi, Marathi and English, and callers who mix them in one sentence.</li>
          <li>Prisma and Timbre cover more Indian languages. Vakya has only been tested in these three.</li>
          <li>The front desk answers in the language the caller starts in and follows them if they switch.</li>
        </ul>
      </>
    ),
  },
  {
    q: "How well does it hear a noisy phone line?",
    text: "On synthetic calls, well. The report card plays the same 24 invented booking calls clean, over an 8 kHz phone line, through street noise and through a phone codec, with and without the business's own names as keyterms, and scores whether the name, day and time came through. On Gnani Prisma v3.0 (6 Nov 2026) the phone line and the codec cost nothing, and only noise as loud as the speech started to lose a field, which the keyterms won back. Ten-digit phone numbers suffer first in noise. The audio is synthetic, so repeat it on real recordings before relying on a number.",
    a: (
      <>
        <p>
          On synthetic calls, well. The{" "}
          <Link className={link} href="/report-card">
            report card
          </Link>{" "}
          plays the same 24 invented booking calls clean, over an 8 kHz phone line, through street noise and through a phone codec, with and without the business&apos;s own names as
          keyterms, and scores whether the name, day and time came through.
        </p>
        <p>On Gnani Prisma v3.0 (6 Nov 2026) the phone line and the codec cost nothing, and only noise as loud as the speech started to lose a field, which the keyterms won back. Ten-digit phone numbers suffer first in noise.</p>
        <p>Even then the audio is synthetic. Repeat it on real recordings before relying on a number. Whatever the table says, the agent reads every number back.</p>
      </>
    ),
  },
  {
    q: "Why does it read everything back?",
    text: "Because phone audio is noisy and a wrong digit in a phone number or a wrong day is a missed patient. Before it books, the agent says the day, date, time and phone number aloud and waits. It commits only after the caller says yes, and a corrected digit is read back again.",
    a: (
      <>
        <p>
          Because phone audio is noisy and a wrong digit in a phone number or a wrong day is a missed patient. Before it books, the agent says the day, date,
          time and phone number aloud and waits.
        </p>
        <p>It commits only after the caller says yes, and a corrected digit is read back again.</p>
      </>
    ),
  },
  {
    q: "Can someone clone my voice without permission?",
    text: "Not through Vakya. Before a voice is cloned the speaker gives consent: one tick on a plain statement by default, or, in strict mode, a one-time sentence with a random code read aloud and matched to the sample. The consent record is stored with the voice, and one-click withdrawal deletes both. A clone made with some other tool is outside what Vakya can stop.",
    a: (
      <>
        <p>
          Not through Vakya. Before a voice is cloned, the speaker gives consent: one tick on a plain statement by default, or, in strict mode, a one-time
          sentence that contains a random code, which is checked against the recording.
        </p>
        <p>
          The consent record is stored with the voice. One-click withdrawal deletes the voice and its recording, and the withdrawal is written to a signed public log. A clone
          made with some other tool is outside what Vakya can stop.
        </p>
      </>
    ),
  },
  {
    q: "How can anyone tell a clip is AI-made?",
    text: "Every clip and front desk reply gets a signed receipt, a C2PA content manifest and an AI-generated label inside the WAV file. Anyone can drop a clip on the Verify page to check it. It is evidence, not proof: a match holds only for the exact file, so a clip that has been re-encoded or edited no longer matches.",
    a: (
      <>
        <p>
          Every clip and front desk reply gets a signed receipt, a C2PA content manifest and an &ldquo;AI-generated&rdquo; label inside the WAV file. Anyone can
          drop a clip on the{" "}
          <Link className={link} href="/verify">
            Verify page
          </Link>{" "}
          to check it, and the receipt signature can be checked offline with the{" "}
          <a className={link} href={`${API_BASE}/.well-known/vakya-receipt-key`}>
            public key
          </a>
          .
        </p>
        <p>
          A match is evidence, not proof: it holds only for the exact file, so a clip that has been re-encoded or edited no longer matches its receipt.
        </p>
      </>
    ),
  },
  {
    q: "Does it remember callers, and can they be forgotten?",
    text: "Yes. A returning caller is greeted with what they said last time (name, language, last booking, a follow-up note). The profile is kept only to serve their next call, is deleted after a retention period, and the owner can delete a caller by number. The profile holds the caller's number in full, so treat the store as personal data.",
    a: (
      <>
        <p>
          Yes. A returning caller is greeted with what they said last time: name, language, last booking, a follow-up note. The profile is kept only to serve their
          next call, is deleted after a retention period, and the owner can delete a caller by number.
        </p>
        <p>The profile holds the caller&apos;s number in full, so treat the store as personal data.</p>
      </>
    ),
  },
  {
    q: "Can it answer phone calls?",
    text: "Partly. The front desk can place a call over Vobiz and speaks first to say it is an AI, and it stops when the person talks over it. The call logic ran on a live trial line earlier; the Vobiz line itself is built and tested with fakes, not yet tried on a real call. Receiving calls on your own number is written but not yet tested live. The missed-call callback, where the front desk rings back a caller who was turned away unanswered, is built and tested with fakes, not yet tried on a live Vobiz number. Only call people who agreed.",
    a: (
      <>
        <p>
          Partly. The front desk can place a call over Vobiz. It speaks first to say it is an AI and stops when the person talks over it. The call logic ran on
          a live trial line earlier; the Vobiz line is built and tested with fakes, not yet tried on a real call.
        </p>
        <p>
          Receiving calls on your own number is written but not yet tested live. The missed-call callback, where the front desk rings back a caller who hung
          up, is built and tested with fakes, not yet tried on a live Vobiz number. Only call people who agreed to be called. There is also a browser web call you
          can open from a link, to try it without a phone.
        </p>
      </>
    ),
  },
  {
    q: "Does it use Google Calendar?",
    text: "It syncs bookings to Google Calendar when a calendar is connected. That sync is written and tested against fakes, not against a real calendar yet. Without a calendar, bookings are kept in Vakya's own list, and the desk's Bookings page shows them either way.",
    a: (
      <>
        <p>
          It syncs bookings to Google Calendar when a calendar is connected. That sync is written and tested against fakes, not against a real calendar yet.
        </p>
        <p>Without a calendar, bookings are kept in Vakya&apos;s own list, and the desk&apos;s Bookings page shows them either way. A calendar failure never blocks a booking.</p>
      </>
    ),
  },
  {
    q: "Is my data shared with anyone?",
    text: "Only the services you give a key to. Your documents, callers and conversations are stored in a folder on your own computer. Data leaves it only through those services: Gnani receives the audio of a call it listens to and the text it speaks, Google (Embeddings) receives the text of a turn it answers and the passages of your documents it needs, Vobiz carries phone calls. This site has no trackers or analytics.",
    a: (
      <>
        <p>
          Only the services you give a key to. Your documents, callers and conversations are stored in a folder on your own computer. This site has no trackers or analytics.
        </p>
        <p>
          Data leaves your machine only through those services: Gnani receives the audio of a call it listens to and the text it speaks, Google (Embeddings) receives the text of
          the turns it handles and the passages of your documents it needs, and Vobiz carries phone calls.
        </p>
      </>
    ),
  },
  {
    q: "What is not ready yet?",
    text: "Gnani Prisma and Timbre first ran live on 5 Nov; the realtime stream, cloning and a real phone call have not run yet. The missed-call callback, inbound calls on your own number and real Google Calendar sync are built and tested with fakes, not yet tried live. The report card is empty until Gnani Prisma has been run, and will use synthetic audio, not real callers. Caller Theatre has passed 9 of 9 scenarios on a scripted model only. Phone numbers are always read back. A receipt matches the exact file it was made for, so a re-encoded or edited copy is not recognised.",
    a: (
      <>
        <p>
          The full list is in the{" "}
          {REPO_PUBLIC ? (
            <a className={link} href={`${REPO}/blob/main/STATUS.md`}>
              status file
            </a>
          ) : (
            "project's status file (shared on request)"
          )}
          . The short version:
        </p>
        <ul>
          <li>Gnani Prisma and Timbre first ran live on 5 Nov; the realtime stream, cloning and a real phone call have not run yet.</li>
          <li>The missed-call callback, inbound calls on your own number and real Google Calendar sync are built and tested with fakes, not yet tried on a live line or calendar.</li>
          <li>The report card is empty until Gnani Prisma has been run, and will use synthetic audio, not real callers.</li>
          <li>Caller Theatre has passed 9 of 9 scenarios, on a scripted model only.</li>
          <li>Phone numbers are always read back, so a misheard digit is caught before a booking.</li>
          <li>A receipt matches the exact file it was made for: a re-encoded or edited copy is not recognised.</li>
        </ul>
      </>
    ),
  },
  {
    q: "Is this legal in India?",
    text: "Vakya builds in the things India's rules on synthetic media and personal data are concerned with: consent before cloning, a retention period, one-click withdrawal, an AI-generated label and a signed receipt on everything it makes, and an AI disclosure at the start of every call. That is a design choice, not a legal opinion. We are a student project, not lawyers: what is lawful for your use, for example calling people, is for you to check.",
    a: (
      <>
        <p>
          Vakya builds in what India&apos;s rules on synthetic media and personal data are concerned with: consent before cloning, a retention period, one-click
          withdrawal, an AI-generated label and a signed receipt on everything it makes, and an AI disclosure at the start of every call.
        </p>
        <p>
          That is a design choice, not a legal opinion. We are a student project, not lawyers, so this is not legal advice. What is lawful for your use, for example
          calling people, is for you to check.
        </p>
      </>
    ),
  },
];

const FAQ_JSON_LD = JSON.stringify({
  "@context": "https://schema.org",
  "@type": "FAQPage",
  mainEntity: FAQ_ITEMS.map(({ q, text }) => ({
    "@type": "Question",
    name: q,
    acceptedAnswer: { "@type": "Answer", text },
  })),
}).replace(/</g, "\\u003c");

export function Faq() {
  return (
    <section id="faq" className="s-container mt-28 md:mt-36" aria-labelledby="faq-title">
      <style>{ENDING_CSS}</style>
      <div className="grid gap-10 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.4fr)] lg:gap-16">
        <div data-reveal>
          <h2 id="faq-title" className="h2 max-w-[14ch]">
            Straight answers.
          </h2>
          <p className="lede mt-5">Who has used it, what it costs, which services it uses, and what is not ready yet.</p>
        </div>
        <div className="border-t border-[var(--s-line)]">
          {FAQ_ITEMS.map(({ q, a }) => (
            <details key={q} className="faq-item group border-b border-[var(--s-line)]" data-reveal>
              <summary className="flex min-h-[4rem] cursor-pointer items-center justify-between gap-6 py-4 text-left s-item font-medium leading-snug text-[var(--s-ink)]">
                <span>{q}</span>
                <span
                  aria-hidden
                  className="relative grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-[var(--s-bar)] transition-colors duration-200 group-hover:border-[var(--s-ink)]"
                >
                  <span className="absolute h-[1.5px] w-3.5 rounded bg-[var(--s-ink)]" />
                  <span className="faq-bar-v absolute h-3.5 w-[1.5px] rounded bg-[var(--s-ink)]" />
                </span>
              </summary>
              <div className="faq-body max-w-[62ch] space-y-3 pb-7 pr-0 s-body leading-relaxed text-[var(--s-ink-2)] sm:pr-14 [&_li]:ml-5 [&_li]:list-disc [&_li]:pl-1 [&_ul]:space-y-1.5">
                {a}
              </div>
            </details>
          ))}
        </div>
      </div>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: FAQ_JSON_LD }} />
    </section>
  );
}

/* ------------------------------------------------------------------ final CTA */

const ctaBase = "group s-btn";

export function FinalCta() {
  return (
    <section id="start" className="s-container mt-24 md:mt-32" aria-labelledby="start-title">
      <style>{ENDING_CSS}</style>
      <Reveal>
        <div className="s-night-panel on-night rounded-2xl px-7 py-14 sm:px-12 md:py-20 lg:px-16">
          {/* The aurora again, dim, on the right: plain gradients painted once. */}
          <div
            aria-hidden
            className="pointer-events-none absolute -right-24 top-1/2 hidden h-[34rem] w-[34rem] -translate-y-1/2 rounded-full opacity-60 blur-3xl md:block"
            style={{ background: "radial-gradient(closest-side, rgba(189,185,247,0.55), rgba(190,241,253,0.25) 55%, transparent 80%)" }}
          />
          <div className="relative max-w-[51.25rem]">
            <Reveal as="p" className="s-eyebrow">Try it</Reveal>
            <SplitHeading id="start-title" text="Make a call" accent="and correct a digit." className="s-title mt-4 max-w-[18ch] !text-white" />
            <Reveal as="p" delay={0.1} className="mt-6 max-w-[52ch] text-base leading-relaxed text-[var(--s-night-mute)]">
              Open the desk, start a web call from your browser and give the agent a wrong number on purpose. It reads the booking back, you fix it, and
              you get a receipt anyone can check.
            </Reveal>
            <Reveal delay={0.2} className="mt-10 flex flex-col gap-3 sm:flex-row sm:flex-wrap">
              <Magnetic strength={0.2}><Link href="/desk/try" prefetch={false} className={`${ctaBase} s-btn--light`}>
                Try a call
                <ArrowUpRight className="h-4 w-4 transition-transform duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5" aria-hidden />
              </Link></Magnetic>
              <Magnetic strength={0.2}><Link href="/verify" className={`${ctaBase} s-btn--ghost-light`}>
                Verify a clip
              </Link></Magnetic>
              <Magnetic strength={0.2}>{REPO_PUBLIC ? (
                <a href={REPO} className={`${ctaBase} s-btn--ghost-light`}>
                  Run it yourself
                  <ArrowUpRight className="h-4 w-4 transition-transform duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5" aria-hidden />
                </a>
              ) : (
                <Link href="/run-it" className={`${ctaBase} s-btn--ghost-light`}>
                  Run it yourself
                </Link>
              )}</Magnetic>
            </Reveal>
          </div>
        </div>
      </Reveal>
    </section>
  );
}

/* ------------------------------------------------------------------ footer */

/** `repo`: points into the GitHub repository, so it is left out while the repository is private (see ./repo). */
type FooterLink = { label: string; href: string; external?: boolean; repo?: boolean };

const FOOTER_COLUMNS: { title: string; links: FooterLink[] }[] = [
  {
    title: "Explore",
    links: ALL_SITE_PAGES.map((p) => ({ label: p.nav, href: p.href })),
  },
  {
    title: "Product",
    links: [
      { label: "Try a call", href: "/desk/try" },
      { label: "Today", href: "/desk" },
      { label: "Calls", href: "/desk/calls" },
      { label: "Report card", href: "/desk/report-card" },
      { label: "Caller Theatre", href: "/desk/theatre" },
      { label: "Check a receipt", href: "/verify" },
    ],
  },
  {
    title: "Developers",
    links: [
      { label: "Source on GitHub", href: REPO, external: true, repo: true },
      { label: "Architecture", href: `${REPO}/blob/main/docs/ARCHITECTURE.md`, external: true, repo: true },
      { label: "Gnani integration", href: `${REPO}/blob/main/docs/GNANI.md`, external: true, repo: true },
      { label: "Phone setup", href: `${REPO}/blob/main/docs/PHONE.md`, external: true, repo: true },
      { label: "Receipt spec", href: `${REPO}/blob/main/docs/RECEIPT_SPEC.md`, external: true, repo: true },
      { label: "Receipt public key", href: `${API_BASE}/.well-known/vakya-receipt-key`, external: true },
    ],
  },
  {
    title: "Project",
    links: [
      { label: "Status", href: `${REPO}/blob/main/STATUS.md`, external: true, repo: true },
      { label: "Measured results", href: `${REPO}/blob/main/docs/RESULTS.md`, external: true, repo: true },
      { label: "Submission answers", href: `${REPO}/blob/main/docs/SUBMISSION.md`, external: true, repo: true },
      { label: "Licences", href: `${REPO}#licence-and-acknowledgements`, external: true, repo: true },
    ],
  },
];

const footLink =
  "inline-flex min-h-[2.75rem] items-center gap-1 s-body text-[var(--s-ink-2)] underline-offset-4 hover:text-[var(--s-ink)] hover:underline";

export function Footer() {
  return (
    <footer className="s-container mt-24 pb-12 md:mt-32" aria-label="Site footer">
      <div className="grid gap-12 border-t border-[var(--s-line)] pt-12 lg:grid-cols-[1fr_2.2fr]" data-reveal>
        <div>
          <Logo size={40} />
          <p className="mt-4 max-w-[36ch] s-body leading-relaxed text-[var(--s-mute)]">
            A voice front desk for clinics, hotels and small businesses in India. Built on Gnani Prisma and Timbre for the Great Indian AI Internship Challenge.
          </p>
          <p className="mt-4 max-w-[36ch] s-small leading-relaxed text-[var(--s-mute)]">
            No trackers or analytics on this site. Cookies are used only to keep you signed in when a hosted setup is configured.
          </p>
        </div>
        <nav aria-label="Footer" className="grid grid-cols-2 gap-x-6 gap-y-8 sm:grid-cols-4">
          {FOOTER_COLUMNS.map((col) => ({ ...col, links: col.links.filter((l) => REPO_PUBLIC || !l.repo) })).filter((col) => col.links.length > 0).map((col) => (
            <div key={col.title} className={col.title === "Developers" ? "order-last col-span-2 sm:order-none sm:col-span-1" : ""}>
              <p className="vk-foot-h">{col.title}</p>
              <ul className="mt-2">
                {col.links.map((l) => (
                  <li key={l.label}>
                    {l.external ? (
                      <a href={l.href} className={footLink}>
                        {l.label}
                        <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden />
                      </a>
                    ) : l.href.startsWith("#") ? (
                      <a href={l.href} className={footLink}>
                        {l.label}
                      </a>
                    ) : (
                      <Link href={l.href} prefetch={l.href.startsWith("/desk") ? false : undefined} className={footLink}>
                        {l.label}
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
      </div>

      <div className="mt-12 grid gap-10 border-t border-[var(--s-line)] pt-10 md:grid-cols-[1fr_1fr_1.4fr]" data-reveal>
        <div>
          <p className="vk-foot-h">Built by</p>
          <ul className="mt-3 space-y-1.5 s-body text-[var(--s-mute)]">
            <li>Ayush Chougula</li>
            <li>Teammate (TBC)</li>
          </ul>
        </div>
        <div>
          <p className="vk-foot-h">Services</p>
          <ul className="mt-3 space-y-1.5 s-body text-[var(--s-mute)]">
            <li>Gnani Prisma, Timbre and voice cloning (speech)</li>
            <li>Embeddings Flash and embeddings (reasoning, search)</li>
            <li>Vobiz (telephony)</li>
          </ul>
        </div>
        <div>
          <p className="vk-foot-h">Notices</p>
          <p className="mt-3 max-w-[40ch] s-small leading-relaxed text-[var(--s-mute)]">
            Speech stack: Gnani AI (Prisma and Timbre). Vakya is not affiliated with Gnani AI.
          </p>
          <p className="mt-3 max-w-[40ch] s-small leading-relaxed text-[var(--s-mute)]">
            Gnani, Google and Vobiz are named as the services Vakya calls. Each keeps its own terms{REPO_PUBLIC ? "; see the licence section linked above." : "."}
          </p>
          <p className="mt-3 max-w-[40ch] s-small leading-relaxed text-[var(--s-mute)]">
            Vakya is a student project. Every booking, caller and number shown is invented.
          </p>
        </div>
      </div>
    </footer>
  );
}
