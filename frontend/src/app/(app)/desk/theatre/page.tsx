import Link from "next/link";
import { ExternalLink } from "lucide-react";
import { PageHeader } from "@/components/ui";
import { Theatre } from "@/components/theatre/Theatre";

/** Caller Theatre inside the desk: the same component and results as the public /theatre page. */
export default function DeskTheatrePage() {
  return (
    <PageHeader
      width="wide"
      title="Caller Theatre"
      description="Synthetic callers run end to end against a front desk and scored from what ended up in the booking system. Open a card to hear or read the call."
      actions={
        <Link href="/theatre" className="inline-flex min-h-10 items-center gap-1.5 text-sm font-medium text-accent-primary hover:underline">
          Public page <ExternalLink className="size-3.5" aria-hidden />
        </Link>
      }
    >
      <Theatre />
    </PageHeader>
  );
}
