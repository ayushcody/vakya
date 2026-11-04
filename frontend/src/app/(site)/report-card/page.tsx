import { PageIntro, NextPage } from "@/components/site/PageIntro";
import { ReportCard } from "@/components/report/ReportCard";
import { pageMetadata } from "@/components/site/pageMeta";

export const metadata = pageMetadata({
  title: "Report card",
  description:
    "The same booking calls played clean, over an 8 kHz phone line, through street noise and phone codecs, scored on whether the name, day and time came through. Run pending: no number is published yet.",
  path: "/report-card",
});

export default function ReportCardPage() {
  return (
    <>
      <PageIntro title="How it will hear a booking on a bad line.">
        The same 24 invented booking calls, played clean, over a phone line and through noise, scored on what a booking needs. The Gnani Prisma run is pending, so the table is empty and says so.
      </PageIntro>
      <section className="s-container mt-12 md:mt-16" aria-label="The report card">
        <ReportCard />
      </section>
      <NextPage from="/report-card" />
    </>
  );
}
