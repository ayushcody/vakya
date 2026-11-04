import Link from "next/link";
import { ExternalLink } from "lucide-react";
import { PageHeader } from "@/components/ui";
import { ReportCard } from "@/components/report/ReportCard";

/** The acoustic-stress report card, inside the desk. The same component and results file as the public /report-card page. */
export default function DeskReportCardPage() {
  return (
    <PageHeader
      width="wide"
      title="Report card"
      description="How well the front desk hears a booking when the line is bad: clean audio, an 8 kHz phone line, street noise and phone codecs, scored on whether the name, day and time came through."
      actions={
        <Link href="/report-card" className="inline-flex min-h-10 items-center gap-1.5 text-sm font-medium text-accent-primary hover:underline">
          Public page <ExternalLink className="size-3.5" aria-hidden />
        </Link>
      }
    >
      <ReportCard />
    </PageHeader>
  );
}
