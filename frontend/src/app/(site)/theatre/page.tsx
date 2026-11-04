import { PageIntro, NextPage } from "@/components/site/PageIntro";
import { Theatre } from "@/components/theatre/Theatre";
import { pageMetadata } from "@/components/site/pageMeta";

export const metadata = pageMetadata({
  title: "Caller Theatre",
  description:
    "Nine synthetic callers run end to end against a front desk and scored automatically from what ended up in the booking system: an elderly Marathi caller, street noise, a wrong digit, a full slot, a cancellation and an Aadhaar number the agent must refuse.",
  path: "/theatre",
});

export default function TheatrePage() {
  return (
    <>
      <PageIntro title="Nine callers, scored on what got booked.">
        Scripted synthetic callers phone a front desk, and the score comes from the booking system afterwards, not from how the call sounded. Failures are shown as plainly as passes.
      </PageIntro>
      <section className="s-container mt-12 md:mt-16" aria-label="Caller Theatre">
        <Theatre />
      </section>
      <NextPage from="/theatre" />
    </>
  );
}
