"use client";

import { Repeat } from "lucide-react";
import type { ListColumn } from "@/components/ui/ListView";
import { cn } from "@/lib/utils";
import { languageLabel, whenLabel } from "../calls/format";
import { FamilyChips } from "@/components/memory";
import type { CallerProfile } from "../calls/callers";

const muted = "text-text-secondary";

/** The reference a caller is linked and listed by (the opaque `caller_ref`, else the profile id). */
export const callerKey = (p: CallerProfile) => p.caller_ref ?? p.id;

const lastCallValue = (p: CallerProfile): number | null => {
  const t = p.last_call_at ? new Date(p.last_call_at).getTime() : NaN;
  return Number.isNaN(t) ? null : t;
};

const peopleText = (p: CallerProfile) => (p.people ?? []).map((x) => x.name).filter(Boolean).join(", ");

/** The last four digits of the masked number ("••••3210" is "3210"), for the "Number ending" column. */
const endingOf = (p: CallerProfile) => {
  const digits = String(p.number_masked ?? "").replace(/\D/g, "");
  return digits ? digits.slice(-4) : "";
};

/** The columns of the Callers list. `booked` is the number of booked appointments per `callerKey`. */
export function callerColumns(booked: Record<string, number>): ListColumn<CallerProfile>[] {
  return [
    {
      id: "name", header: "Name", width: "minmax(140px, 1.6fr)", minWidth: 140, required: true,
      stacked: { line: 1, slot: "fill" },
      sortValue: (p) => (p.display_name ?? "").toLowerCase() || null,
      cell: (p) => (
        <span className="flex min-w-0 items-center gap-1.5">
          <span title={p.display_name ?? undefined} className={cn("truncate text-sm", p.display_name ? "font-medium text-text-primary" : muted)}>
            {p.display_name || "Name not given"}
          </span>
          {p.call_count > 1 && (
            <span role="img" aria-label="Has called before" title={`Called ${p.call_count} times`} className="shrink-0 text-accent-primary">
              <Repeat aria-hidden className="size-3.5" />
            </span>
          )}
        </span>
      ),
    },
    {
      id: "number", header: "Number ending", width: "124px", minWidth: 124, priority: 2,
      sortValue: (p) => endingOf(p) || null,
      cell: (p) => <span className="block whitespace-nowrap text-sm tabular-nums text-text-primary">{endingOf(p) ? `••••${endingOf(p)}` : "—"}</span>,
    },
    {
      id: "language", header: "Language", width: "minmax(88px, 0.8fr)", minWidth: 88, priority: 4,
      sortValue: (p) => languageLabel(p.preferred_language),
      cell: (p) => <span className={cn("block truncate text-sm", muted)}>{languageLabel(p.preferred_language) ?? "—"}</span>,
    },
    {
      id: "people", header: "Family", width: "minmax(160px, 1.8fr)", minWidth: 160, priority: 5,
      stacked: { line: 2, slot: "fill" },
      sortValue: (p) => peopleText(p).toLowerCase() || null,
      cell: (p) => <FamilyChips people={p.people} />,
    },
    {
      id: "last", header: "Last call", width: "minmax(148px, 1.2fr)", minWidth: 148, required: true,
      stacked: { line: 1, slot: "end" },
      sortValue: lastCallValue,
      cell: (p) => <span className="block truncate whitespace-nowrap text-sm text-text-primary">{p.last_call_at ? whenLabel(p.last_call_at) : "—"}</span>,
    },
    {
      id: "bookings", header: "Bookings", width: "88px", minWidth: 88, align: "end", priority: 3,
      stacked: { line: 2, slot: "end" },
      sortValue: (p) => booked[callerKey(p)] ?? 0,
      cell: (p) => <span className={cn("text-sm tabular-nums", muted)}>{booked[callerKey(p)] ?? 0}</span>,
    },
  ];
}
