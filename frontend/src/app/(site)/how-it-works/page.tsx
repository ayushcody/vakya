import { PageIntro, NextPage } from "@/components/site/PageIntro";
import { Architecture, TurnRules } from "@/components/site/Architecture";
import { Consent } from "@/components/site/Sections";
import { pageMetadata } from "@/components/site/pageMeta";

export const metadata = pageMetadata({
  title: "How it works",
  description:
    "The call path from an 8 kHz phone line through Gnani Prisma, a turn manager that reads every number back, a language model with tools and Gnani Timbre, with consent, a content manifest and signed receipts beside it.",
  path: "/how-it-works",
});

export default function HowItWorksPage() {
  return (
    <>
      <PageIntro title="From a ringing phone to a signed receipt.">
        What happens between the caller&apos;s first word and the booking, and what stays on record afterwards.
      </PageIntro>
      <Architecture />
      <TurnRules />
      <Consent />
      <NextPage from="/how-it-works" />
    </>
  );
}
