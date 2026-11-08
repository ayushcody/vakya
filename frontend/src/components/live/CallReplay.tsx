"use client";

import { useMemo } from "react";
import useSWR from "swr";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { turnsToEvents, type ReplayTurn } from "@/lib/callReplay";
import { initialLive, reduceLive } from "@/lib/liveCall";
import { LANG_NAME } from "@/lib/liveCall";
import { OutcomePill } from "@/app/(app)/desk/calls/CallRow";
import { durationLabel } from "@/app/(app)/desk/calls/format";
import type { CallDetail } from "@/app/(app)/desk/calls/types";
import { formatDayTime } from "@/lib/formatDate";
import { useDelivery } from "./Delivery";
import { Legend, LiveTranscript } from "./LiveTranscript";

type Replayable = Omit<CallDetail, "transcript"> & { transcript: ReplayTurn[] };

/**
 * A finished call in the live call's own view: the same bubbles, language underlines and action cards, built from what the desk stored
 * (GET /api/calls/{id}). The WhatsApp and calendar chips are read now, so they show where each confirmation ended up.
 * What was not stored (how long the first audio took) is not shown.
 */
export function CallReplay({ id }: { id: string }) {
  const { data, error, isLoading } = useSWR<Replayable>(`/api/calls/${encodeURIComponent(id)}`, (p: string) => api.get<Replayable>(p), { revalidateOnFocus: false });
  const delivery = useDelivery(false, data?.agent_id ?? undefined);
  const state = useMemo(() => {
    if (!data) return initialLive();
    const events = turnsToEvents(data.transcript ?? [], { agent_id: data.agent_id ?? "", conversation_id: data.id, language: data.language });
    return reduceLive(initialLive(), events, 0);
  }, [data]);

  if (isLoading) return <Skeleton className="h-64 w-full rounded-2xl" />;
  if (error || !data) return <p role="alert" className="text-accent-error">Could not load this call. It may have been removed.</p>;

  const when = data.when ? new Date(data.when) : null;
  const day = when ? `${when.getFullYear()}-${String(when.getMonth() + 1).padStart(2, "0")}-${String(when.getDate()).padStart(2, "0")}` : null;
  const time = when ? `${String(when.getHours()).padStart(2, "0")}:${String(when.getMinutes()).padStart(2, "0")}` : null;
  const facts = [
    data.caller_name || data.caller,
    day ? formatDayTime(day, time) : null,
    durationLabel(data.duration_s),
    data.language ? LANG_NAME[data.language] ?? data.language : null,
  ].filter(Boolean);

  return (
    <div className="space-y-5" data-testid="call-replay">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 rounded-2xl border border-border bg-bg-surface p-3 sm:px-5 sm:py-3.5">
        <div className="min-w-0">
          <p className="truncate text-[15px] text-text-primary">{facts.join(" · ")}</p>
          <p className="text-sm text-text-secondary">{data.agent_name}</p>
        </div>
        <div className="flex items-center gap-3">
          {data.outcome.detail && <span className="hidden text-sm text-text-secondary sm:inline">{data.outcome.detail}</span>}
          <OutcomePill outcome={data.outcome} />
        </div>
      </div>
      {state.lines.length === 0 ? (
        <p className="py-10 text-center text-text-secondary">Nothing was said on this call.</p>
      ) : (
        <div className="space-y-4">
          <LiveTranscript lines={state.lines} now={Number.MAX_SAFE_INTEGER} agentId={data.agent_id ?? undefined} delivery={delivery} />
          <div className="border-t border-border pt-4"><Legend /></div>
        </div>
      )}
      <Link href="/desk/calls" className="inline-flex min-h-10 items-center gap-1.5 text-sm font-medium text-accent-primary hover:underline">
        <ArrowLeft aria-hidden className="size-4" />All calls
      </Link>
    </div>
  );
}
