import type { Metadata } from "next";
import { Hero } from "@/components/site/Hero";
import { DemoVideo } from "@/components/site/DemoVideo";
import { BuiltOnGnani, CallFlow, Limitations, ReportTeaser, StackStrip, Team, TrustGrid } from "@/components/site/Landing";
import { FinalCta } from "@/components/site/Ending";
import { ForEveryDesk } from "@/components/site/ForEveryDesk";
import { WhatVakyaDoes } from "@/components/site/WhatVakyaDoes";
import { WholeCall } from "@/components/site/WholeCall";
import { ScheduleVisualiser } from "@/components/site/ScheduleVisualiser";
import { LegacyHashRedirect } from "@/components/site/LegacyHashRedirect";

const TITLE = "Vakya: call once, it's booked";
const DESCRIPTION =
  "A voice front desk for Indian clinics, hotels, salons and other small businesses, built on Gnani Prisma and Timbre. It answers in Hindi, Marathi and English, checks the schedule, reads every number back, books only on a spoken yes and leaves a signed receipt. Built for noisy 8 kHz phone lines; the noise test is published once it has run on Gnani Prisma.";

// Server component so the page can export real metadata; the interactive pieces are client components below.
export const metadata: Metadata = {
  title: { absolute: TITLE },
  description: DESCRIPTION,
  openGraph: {
    type: "website",
    siteName: "Vakya",
    title: TITLE,
    description: DESCRIPTION,
    // The 1200x630 card rendered by app/opengraph-image.tsx. It is named here because a page-level openGraph object
    // replaces the file-based image that the root layout would otherwise have passed down.
    images: [{ url: "/opengraph-image", width: 1200, height: 630, alt: "Vakya: call once, it's booked" }],
  },
  alternates: { canonical: "/" },
  twitter: { card: "summary_large_image", title: TITLE, description: DESCRIPTION, images: ["/opengraph-image"] },
};

const jsonLd = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: "Vakya",
  applicationCategory: "BusinessApplication",
  description: DESCRIPTION,
};

export default function HomePage() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      {/* Old single-page links (/#faq, /#rig-check ...) move to the page that now holds that section. */}
      <LegacyHashRedirect />
      <Hero />
      <StackStrip />
      <DemoVideo />
      <WholeCall />
      <CallFlow />
      <WhatVakyaDoes />
      <ScheduleVisualiser />
      <ForEveryDesk />
      <BuiltOnGnani />
      <ReportTeaser />
      <TrustGrid />
      <Limitations />
      <FinalCta />
      <Team />
    </>
  );
}
