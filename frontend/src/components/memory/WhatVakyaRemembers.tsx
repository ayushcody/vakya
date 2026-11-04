"use client";

import useSWR from "swr";
import { Brain } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { CALLER_OUTCOME_LABEL, bookingLabel, languageLabel, whenLabel } from "@/app/(app)/desk/calls/format";
import { FactChips, PeopleChips } from "./RememberedChips";
import type { CallerProfile } from "./types";

/** The words for the details of the call in progress, as the agent holds them ("name", "booking for", "phone"...). */
const SLOT_ORDER = ["name", "booking for", "phone", "date", "time", "doctor", "reason", "email"];

function slotText(key: string, value: string): string {
  return key === "phone" ? `••••${value.replace(/\D/g, "").slice(-4)}` : value;
}

export interface WhatVakyaRemembersProps {
  /** The caller's profile from earlier calls (null: a first call or a number not known). */
  profile?: CallerProfile | null;
  /** What the caller has said on THIS call (the caller details form, masked): shown first. */
  slots?: Record<string, string> | null;
  title?: string;
  className?: string;
}

/**
 * "What Vakya remembers": the details of this call so far and what is kept from earlier calls (name, language, the people on
 * the number, the trade's details, the last booking, the notes for next time). Presentational and small enough to sit beside
 * the live call; `useRememberedCaller` fetches the profile of a conversation.
 */
export function WhatVakyaRemembers({ profile, slots, title = "What Vakya remembers", className }: WhatVakyaRemembersProps) {
  const onCall = Object.entries(slots ?? {})
    .filter(([, v]) => v)
    .sort(([a], [b]) => (SLOT_ORDER.indexOf(a) + 1 || 99) - (SLOT_ORDER.indexOf(b) + 1 || 99));
  const booking = bookingLabel(profile?.last_booking);
  const language = languageLabel(profile?.preferred_language);
  const empty = onCall.length === 0 && !profile;
  return (
    <section aria-label={title} data-testid="remembers-panel" className={cn("rounded-xl border border-border bg-bg-surface p-4", className)}>
      <h3 className="flex items-center gap-2 text-sm font-semibold text-text-primary">
        <Brain aria-hidden className="size-4 text-text-secondary" />
        {title}
      </h3>
      {empty && <p className="mt-2 text-sm text-text-secondary">Nothing yet. Details the caller gives are kept here as they are said.</p>}
      {onCall.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary">On this call</p>
          <ul className="mt-1.5 flex flex-wrap gap-1.5" data-testid="call-slots">
            {onCall.map(([k, v]) => (
              <li key={k} className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-bg-base px-2.5 py-1 text-xs">
                <span className="text-text-secondary">{k}</span>
                <span className="min-w-0 break-words font-medium text-text-primary">{slotText(k, v)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {profile && (
        <div className="mt-3 space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary">From earlier calls</p>
          <p className="text-sm text-text-primary">
            <span className="font-medium">{profile.display_name || "Name not given"}</span>
            <span className="text-text-secondary"> · {profile.number_masked}{language ? ` · ${language}` : ""} · {profile.call_count === 1 ? "called once" : `called ${profile.call_count} times`}</span>
          </p>
          <PeopleChips people={profile.people} last={profile.last_person} />
          <FactChips facts={profile.facts} />
          {(booking || profile.last_outcome) && (
            <p className="text-sm text-text-primary">
              {booking && <>Last booking {booking}{profile.last_booking?.purpose ? ` · ${profile.last_booking.purpose}` : ""}. </>}
              {profile.last_call_at && profile.last_outcome && (
                <span className="text-text-secondary">{whenLabel(profile.last_call_at)} · {CALLER_OUTCOME_LABEL[profile.last_outcome] ?? profile.last_outcome}</span>
              )}
            </p>
          )}
          {profile.follow_ups.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-5 text-sm text-text-primary" aria-label="To ask next time">
              {profile.follow_ups.map((f, i) => <li key={i}>{f.note}</li>)}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

/** The profile of the caller of one conversation (null while loading, for a first call, or when caller memory is off). */
export function useRememberedCaller(conversationId?: string | null): CallerProfile | null {
  const key = conversationId ? `/api/callers?conversation_id=${encodeURIComponent(conversationId)}&limit=1` : null;
  const { data } = useSWR<{ callers?: CallerProfile[] }>(key, (p: string) => api.get<{ callers?: CallerProfile[] }>(p), {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  });
  return Array.isArray(data?.callers) && data.callers.length > 0 ? data.callers[0] : null;
}
