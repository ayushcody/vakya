"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Magnetic, Reveal, SplitHeading } from "./motion";

/* The hero is the aurora: soft violet, blue and warm rays radiating from a white core behind a centred two-line serif
   headline, dissolving into the white page. The art is plain CSS (see .vk-aurora in site.css): layered radial and conic
   gradients, blurred, with a slow 40 s turn that stops under reduced motion. It costs no download and never competes
   with the headline, which is usually the Largest Contentful Paint, so the word rise is short and nothing else hides it.
   Hero stack: headline, one sentence, two buttons. What it is built on sits in its own strip below. */
export function Hero() {
  return (
    <section id="top" aria-labelledby="hero-title" className="relative isolate pt-28 md:pt-36">
      <div aria-hidden className="vk-aurora">
        <div className="vk-aurora__core" />
        <div className="vk-aurora__rays" />
      </div>

      <div className="s-container text-center">
        <SplitHeading
          as="h1"
          id="hero-title"
          immediate
          text="Call once,"
          accent="it's booked."
          break
          className="display mx-auto max-w-[22ch]"
        />
        <Reveal immediate delay={0.4} y={12}>
          <p className="mx-auto mt-6 max-w-[48ch] text-balance text-base leading-relaxed text-[var(--s-ink-2)] md:text-lg">
            Vakya answers the phone for a clinic, a hotel or any front desk in Hindi, Marathi and English, checks the schedule, reads every detail back, and books only after a spoken yes.
          </p>
        </Reveal>
        <Reveal immediate delay={0.55} y={12} className="mt-8 flex flex-wrap justify-center gap-3">
          <Magnetic strength={0.2}>
            <Link href="/desk/try" prefetch={false} className="group s-btn s-btn--primary s-btn--chip">
              <span className="s-btn__chip" aria-hidden>
                <ArrowRight className="h-4 w-4" />
              </span>
              Try a call
            </Link>
          </Magnetic>
          <Link href="/report-card" className="s-btn s-btn--secondary">
            See the report card
          </Link>
        </Reveal>
      </div>
    </section>
  );
}
