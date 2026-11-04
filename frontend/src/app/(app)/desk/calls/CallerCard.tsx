"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { Globe, Pencil, UserRound } from "lucide-react";
import { toast } from "sonner";
import { Badge, Button } from "@/components/ui";
import { ConfirmDialog } from "@/components/agents/ConfirmDialog";
import { errMessage } from "@/components/desk/format";
import { CallerEditor, FactChips, PeopleChips } from "@/components/memory";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { CALLER_OUTCOME_LABEL, bookingLabel, languageLabel, whenLabel } from "./format";
import type { CallerProfile } from "./callers";

interface BookingRow {
  id: string | null;
  name: string | null;
  service: string | null;
  reason: string | null;
  start: string | null;
  date: string | null;
  time: string | null;
  status: "booked" | "cancelled";
}

/** Upcoming bookings first (soonest first), then the past ones (latest first). */
export function orderBookings(rows: BookingRow[], now: number = Date.now()): BookingRow[] {
  const at = (b: BookingRow) => (b.start ? new Date(b.start).getTime() : NaN);
  const upcoming = rows.filter((b) => !(at(b) < now)).sort((a, b) => (at(a) || 0) - (at(b) || 0));
  const past = rows.filter((b) => at(b) < now).sort((a, b) => at(b) - at(a));
  return [...upcoming, ...past];
}

function Bookings({ callerRef }: { callerRef: string }) {
  const { data, error, isLoading } = useSWR<{ bookings?: BookingRow[] }>(
    `/api/bookings?caller_ref=${encodeURIComponent(callerRef)}`,
    (p: string) => api.get<{ bookings?: BookingRow[] }>(p),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
  const rows = orderBookings(Array.isArray(data?.bookings) ? data.bookings : []);
  if (isLoading) return <p className="mt-2 text-sm text-text-secondary" aria-busy="true">Loading bookings…</p>;
  if (error) return <p className="mt-2 text-sm text-text-secondary">Could not load the bookings.</p>;
  if (rows.length === 0) return <p className="mt-2 text-sm text-text-secondary">No bookings from this number yet.</p>;
  return (
    <ul className="mt-2 divide-y divide-border rounded-lg border border-border" data-testid="caller-bookings">
      {rows.map((b, i) => (
        <li key={b.id ?? i} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-2 text-sm">
          <span className="min-w-0 break-words text-text-primary">
            <span className="font-medium">{bookingLabel({ date: b.date ?? undefined, time: b.time ?? undefined }) ?? "Time not set"}</span>
            {b.name ? ` · ${b.name}` : ""}{b.service ? ` · ${b.service}` : ""}{b.reason ? ` · ${b.reason}` : ""}
          </span>
          <Badge tone={b.status === "cancelled" ? "neutral" : "success"}>{b.status === "cancelled" ? "Cancelled" : "Booked"}</Badge>
        </li>
      ))}
    </ul>
  );
}

/**
 * What the receptionist remembers about the person on this call (backend/services/caller_profiles.py): their name, language, the
 * people on the number, the details of the agent's trade, last booking and anything the agent noted to ask again. The owner
 * can make it forget them. With `manage` (the Callers page) the owner also edits it inline and sees the bookings made from this
 * number; `highlighted` marks the caller a link pointed at.
 */
export function CallerCard({ profile, onForgotten, manage = false, highlighted = false, onChanged }: {
  profile: CallerProfile;
  onForgotten: () => void | Promise<unknown>;
  manage?: boolean;
  highlighted?: boolean;
  onChanged?: () => void | Promise<unknown>;
}) {
  const [asking, setAsking] = useState(false);
  const [editing, setEditing] = useState(false);
  const [opened, setOpened] = useState<boolean | null>(null);   // the owner's choice; until then the linked caller shows its bookings
  const showBookings = opened ?? highlighted;
  const ref = useRef<HTMLElement>(null);
  const booking = bookingLabel(profile.last_booking);
  const language = languageLabel(profile.preferred_language);
  const name = profile.display_name || "Name not given";
  const callerRef = profile.caller_ref ?? profile.id;

  useEffect(() => {
    if (highlighted) ref.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [highlighted]);

  const forget = async () => {
    try {
      await api.del(`/api/callers/${encodeURIComponent(profile.id)}`);
      toast.success("Forgotten. The next call from this number starts fresh.");
      setAsking(false);
      await onForgotten();
    } catch (e) {
      toast.error(errMessage(e, "Could not forget this caller. Try again."));
    }
  };

  const rows: [string, string][] = [
    ["Number", profile.number_masked],
    ["Language", language ?? "Not known yet"],
    ["Last call", profile.last_call_at ? `${whenLabel(profile.last_call_at)}${profile.last_outcome ? ` · ${CALLER_OUTCOME_LABEL[profile.last_outcome] ?? profile.last_outcome}` : ""}` : "Unknown"],
    ["Last booking", booking ? `${booking}${profile.last_booking?.purpose ? ` · ${profile.last_booking.purpose}` : ""}` : "None"],
  ];
  const calls = [...(profile.calls ?? [])].reverse();

  return (
    <section
      ref={ref}
      id={`caller-${callerRef}`}
      aria-label="Caller profile"
      data-testid="caller-card"
      data-highlighted={highlighted ? "true" : undefined}
      className={cn("scroll-mt-20 rounded-xl border border-border bg-bg-surface p-4", highlighted && "border-text-primary ring-2 ring-text-primary/30")}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span aria-hidden className="grid size-9 shrink-0 place-items-center rounded-lg bg-bg-elevated text-text-secondary">
            <UserRound className="size-[18px]" />
          </span>
          <div className="min-w-0">
            <h3 className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-semibold text-text-primary">
              <span className="truncate">{name}</span>
              {profile.source === "web call" && (
                <Badge tone="neutral" title="The number was given on a call in the browser and has not been checked"><Globe className="size-3" aria-hidden />Web call</Badge>
              )}
            </h3>
            <p className="text-xs text-text-secondary">
              {profile.call_count === 1 ? "Called once" : `Called ${profile.call_count} times`} · remembered to serve them on their next call
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {manage && (
            <Button variant="outline" size="sm" leftIcon={<Pencil className="size-3.5" />} onClick={() => setEditing((v) => !v)} aria-expanded={editing}>
              {editing ? "Close editor" : "Edit"}
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => setAsking(true)}>
            Forget this caller
          </Button>
        </div>
      </div>
      <dl className="mt-3 grid gap-y-1.5 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="flex gap-2">
            <dt className="w-24 shrink-0 text-text-secondary">{k}</dt>
            <dd className="min-w-0 break-words text-text-primary">{v}</dd>
          </div>
        ))}
      </dl>
      {(profile.people?.length ?? 0) > 0 && (
        <div className="mt-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary">People on this number</p>
          <PeopleChips className="mt-1.5" people={profile.people} last={profile.last_person} />
        </div>
      )}
      {(profile.facts?.length ?? 0) > 0 && (
        <div className="mt-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary">Remembered details</p>
          <FactChips className="mt-1.5" facts={profile.facts} />
        </div>
      )}
      {profile.follow_ups.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary">To ask next time</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-text-primary">
            {profile.follow_ups.map((f, i) => <li key={i}>{f.note}</li>)}
          </ul>
        </div>
      )}
      {manage && profile.summary && (
        <p className="mt-3 text-sm text-text-primary"><span className="text-text-secondary">Last call: </span>{profile.summary}</p>
      )}
      {manage && editing && (
        <CallerEditor
          profile={profile}
          onCancel={() => setEditing(false)}
          onSaved={async () => { setEditing(false); await onChanged?.(); }}
        />
      )}
      {manage && (
        <div className="mt-3 border-t border-border pt-3">
          <button
            type="button"
            className="text-sm font-medium text-text-primary underline-offset-2 hover:underline"
            aria-expanded={showBookings}
            onClick={() => setOpened(!showBookings)}
          >
            {showBookings ? "Hide bookings" : "Show bookings"}
          </button>
          {showBookings && <Bookings callerRef={callerRef} />}
          {calls.length > 0 && (
            <details className="mt-3 text-sm">
              <summary className="cursor-pointer text-text-primary">Call history ({calls.length})</summary>
              <ul className="mt-1.5 space-y-1 text-text-secondary" data-testid="caller-calls">
                {calls.map((c) => (
                  <li key={c.conversation_id}>
                    <Link href={`/desk/calls?call=${encodeURIComponent(c.conversation_id)}`} className="text-text-primary underline-offset-2 hover:underline">
                      {c.at ? whenLabel(c.at) : "Unknown time"}
                    </Link>
                    {" · "}{c.returning ? "Recognised on pickup" : "First call from this number"}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      <p className="mt-3 text-xs text-text-secondary">
        {profile.consent_note.charAt(0).toUpperCase() + profile.consent_note.slice(1).replace("CALLER_PROFILE_RETENTION_DAYS", `${profile.retention_days} days`)}.
      </p>
      <ConfirmDialog
        open={asking}
        title="Forget this caller?"
        description="Their name, language, last booking, details and notes are deleted now. The call transcripts stay. The next call from this number is treated as a first call."
        confirmLabel="Forget this caller"
        keepLabel="Keep"
        onCancel={() => setAsking(false)}
        onConfirm={forget}
      />
    </section>
  );
}
