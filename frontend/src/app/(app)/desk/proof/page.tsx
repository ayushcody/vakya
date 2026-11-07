"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { ArrowUpRight } from "lucide-react";
import { PageHeader } from "@/components/ui";
import { ReportCard } from "@/components/report/ReportCard";
import { Theatre } from "@/components/theatre/Theatre";
import { cn } from "@/lib/utils";

const TABS = [
  { id: "noise", label: "Noise report card" },
  { id: "theatre", label: "Caller Theatre" },
  { id: "receipt", label: "Check a receipt" },
] as const;
type TabId = (typeof TABS)[number]["id"];

/** Proof: the evidence in one place, out of the way of the daily desk. The tab lives in ?tab= so a link can open one. */
function Proof() {
  const params = useSearchParams();
  const router = useRouter();
  const tab: TabId = (TABS.find((t) => t.id === params.get("tab"))?.id ?? "noise") as TabId;

  return (
    <PageHeader
      width="wide"
      title="Proof"
      description="How well the front desk hears a bad line, how it handles hard callers, and a way for anyone to check a recording it made."
    >
      <div role="tablist" aria-label="Proof" className="mb-6 flex flex-wrap gap-1 border-b border-border">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => router.replace(`/desk/proof?tab=${t.id}`, { scroll: false })}
            className={cn(
              "-mb-px min-h-10 border-b-2 px-3 text-sm font-medium transition-colors",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary",
              tab === t.id ? "border-text-primary text-text-primary" : "border-transparent text-text-secondary hover:text-text-primary"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" aria-label={TABS.find((t) => t.id === tab)?.label}>
        {tab === "noise" && <ReportCard />}
        {tab === "theatre" && <Theatre />}
        {tab === "receipt" && (
          <div className="max-w-xl rounded-xl border border-border bg-bg-surface p-5">
            <p className="text-sm text-text-secondary">
              Every clip the front desk speaks carries a signed receipt. Anyone can drop a recording on the public page and see who made it,
              when, and whether it was changed. No sign-in needed, so you can share the link.
            </p>
            <Link href="/verify" className="mt-4 inline-flex min-h-10 items-center gap-1.5 text-sm font-medium text-accent-primary hover:underline">
              Open the receipt check <ArrowUpRight className="size-3.5" aria-hidden />
            </Link>
          </div>
        )}
      </div>
    </PageHeader>
  );
}

export default function DeskProofPage() {
  return (
    <Suspense>
      <Proof />
    </Suspense>
  );
}
