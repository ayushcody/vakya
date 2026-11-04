"use client";

import Link from "next/link";
import { ArrowRight, PhoneCall } from "lucide-react";
import { Badge, Button, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { OUTCOME_LABEL, OUTCOME_TONE, callerLabel, durationLabel, whenLabel } from "@/app/(app)/desk/calls/format";
import { useCalls } from "./calls";
import { errMessage } from "./format";

/** The last few calls, each with what happened. The full list, transcripts and live listening are on the Calls page. */
export function RecentCalls({ limit = 5 }: { limit?: number }) {
  const { data, error, isLoading, mutate } = useCalls(30, 100);
  const calls = (data?.calls ?? []).slice(0, limit);

  return (
    <section aria-labelledby="recent-calls-title">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h2 id="recent-calls-title" className="text-sm font-semibold text-text-primary">Recent calls</h2>
        <Link href="/desk/calls" className="inline-flex min-h-8 items-center gap-1 text-sm font-medium text-accent-primary hover:underline">
          All calls <ArrowRight className="size-3.5" aria-hidden />
        </Link>
      </div>
      {error && !data ? (
        <ErrorState title="Could not load the calls" message={errMessage(error, "Check that the API is running.")} onRetry={() => mutate()} />
      ) : isLoading ? (
        <div className="space-y-2" aria-busy="true" aria-label="Loading calls">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : calls.length === 0 ? (
        <EmptyState
          icon={<PhoneCall />}
          title="No calls yet"
          description="When someone rings the front desk, or you try a call, it shows up here."
          action={<Link href="/desk/try"><Button size="sm">Try a call</Button></Link>}
        />
      ) : (
        <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-bg-surface">
          {calls.map((c) => (
            <li key={c.id}>
              <Link href={`/desk/calls?call=${encodeURIComponent(c.id)}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 hover:bg-bg-elevated/60 sm:px-5">
                <span className="text-sm font-medium text-text-primary">{whenLabel(c.when)}</span>
                <Badge tone={OUTCOME_TONE[c.outcome.key]} dot>{OUTCOME_LABEL[c.outcome.key] ?? c.outcome.label}</Badge>
                <span className="min-w-0 flex-1 truncate text-sm text-text-secondary">
                  {callerLabel(c)}{durationLabel(c.duration_s) ? ` · ${durationLabel(c.duration_s)}` : ""}{c.preview ? ` · “${c.preview}”` : ""}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
