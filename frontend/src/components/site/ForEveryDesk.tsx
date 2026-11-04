"use client";

import { DeskIllustration } from "@/components/illustrations/DeskIllustration";
import { ACCENTS, DESK_IDS, deskTypes } from "@/components/illustrations/deskTypes";
import { Item, Reveal, SplitHeading, Stagger } from "./motion";

/** Right after the call flow: the eight kinds of front desk Vakya can be set up as, each with its own illustration. */
export function ForEveryDesk() {
  return (
    <section id="desks" aria-labelledby="desks-title" className="s-container mt-24 md:mt-32">
      <div className="flex flex-col items-center text-center">
        <SplitHeading id="desks-title" text="For every" accent="front desk." className="h2 max-w-[14ch]" />
        <Reveal as="p" className="lede mt-5 max-w-[52ch]">
          Clinics, hotels, salons, restaurants, offices, institutes, service centres and gyms. Pick yours, name it, and it answers.
        </Reveal>
      </div>
      <Stagger as="ul" aria-label="Kinds of front desk" className="mt-10 grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-8">
        {DESK_IDS.map((id) => {
          const type = deskTypes[id];
          return (
            <Item as="li" key={id} className="min-w-0">
              <figure className="m-0">
                <div className="overflow-hidden rounded-xl border border-[var(--s-line)]" style={{ background: ACCENTS[type.accent].tint }}>
                  <DeskIllustration id={id} variant="scene" />
                </div>
                <figcaption className="mt-2.5 text-center text-sm font-semibold leading-tight text-black">{type.label}</figcaption>
              </figure>
            </Item>
          );
        })}
      </Stagger>
    </section>
  );
}
