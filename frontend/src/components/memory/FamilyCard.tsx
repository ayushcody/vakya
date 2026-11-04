"use client";

import { useId, useMemo, useState } from "react";
import useSWR from "swr";
import { Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui";
import { ConfirmDialog } from "@/components/agents/ConfirmDialog";
import { errMessage } from "@/components/desk/format";
import { api } from "@/lib/api";
import { formatDayTime } from "@/lib/formatDate";
import { cn } from "@/lib/utils";
import { FactChips } from "./RememberedChips";
import type { CallerPerson, CallerProfile } from "./types";

const input = "h-9 w-full min-w-0 rounded-lg border border-border bg-bg-base px-2.5 text-sm text-text-primary outline-none focus-visible:border-text-secondary";
const RELATIONS = ["self", "mother", "father", "son", "daughter", "wife", "husband", "brother", "sister", "grandmother", "grandfather", "friend", "child", "other"];
const BANDS: [string, string][] = [["", "Age band"], ["child", "Child"], ["teen", "Teen"], ["adult", "Adult"], ["senior", "60+"]];
const chip = "inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-bg-base px-2.5 py-1 text-xs text-text-primary";

export const ageBandLabel = (band?: string | null) => (band ? BANDS.find(([v]) => v === band)?.[1] ?? band : null);
export const relationLabel = (relation?: string | null) => (relation ? relation.charAt(0).toUpperCase() + relation.slice(1) : null);
const firstName = (name: string) => name.trim().split(/\s+/)[0] ?? name;

/** Two names are one person when all the words of one are in the other ("Sunita" and "Sunita Kulkarni"). */
export function samePerson(a?: string | null, b?: string | null): boolean {
  const x = (a ?? "").toLowerCase().split(/\s+/).filter(Boolean);
  const y = (b ?? "").toLowerCase().split(/\s+/).filter(Boolean);
  return x.length > 0 && y.length > 0 && (x.every((w) => y.includes(w)) || y.every((w) => x.includes(w)));
}

/** The family of a number in a list row: "Sunita · mother", "Rahul · self", at most `max` and a "+2" for the rest. */
export function FamilyChips({ people, max = 3, className }: { people?: CallerPerson[]; max?: number; className?: string }) {
  if (!people || people.length === 0) return <span className="text-sm text-text-secondary">—</span>;
  const shown = people.slice(0, max);
  const label = people.map((p) => (p.relation ? `${p.name}, ${p.relation}` : p.name)).join("; ");
  return (
    <ul className={cn("flex min-w-0 flex-nowrap items-center gap-1 overflow-hidden", className)} aria-label={`Family: ${label}`} data-testid="family-chips" title={label}>
      {shown.map((p) => (
        <li key={p.name} className={cn(chip, "shrink-0 whitespace-nowrap py-0.5")}>
          <span className="font-medium">{firstName(p.name)}</span>
          {p.relation && <>{" "}<span className="text-text-secondary">· {p.relation}</span></>}
        </li>
      ))}
      {people.length > max && <li className="shrink-0 text-xs text-text-secondary">+{people.length - max}</li>}
    </ul>
  );
}

interface BookingRow { id: string | null; name: string | null; reason: string | null; start: string | null; date: string | null; time: string | null; status: "booked" | "cancelled" }

function dayOf(b: { date?: string | null; time?: string | null }) {
  return formatDayTime(b.date ?? undefined, b.time ?? undefined);
}

function PersonCard({ profile, person, bookings, onChanged }: { profile: CallerProfile; person: CallerPerson; bookings: BookingRow[]; onChanged: (p: CallerProfile) => void | Promise<unknown> }) {
  const [editing, setEditing] = useState(false);
  const [asking, setAsking] = useState(false);
  const [name, setName] = useState(person.name);
  const [relation, setRelation] = useState(person.relation ?? "");
  const [band, setBand] = useState(person.age_band ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const url = `/api/callers/${encodeURIComponent(profile.id)}/people/${encodeURIComponent(person.name)}`;

  const mine = useMemo(() => bookings.filter((b) => b.status === "booked" && samePerson(b.name, person.name)), [bookings, person.name]);
  const now = Date.now();
  const at = (b: BookingRow) => (b.start ? new Date(b.start).getTime() : NaN);
  const next = mine.filter((b) => at(b) >= now).sort((a, b) => at(a) - at(b))[0];
  const lastBooked = mine.filter((b) => at(b) < now).sort((a, b) => at(b) - at(a))[0];
  const last = person.last_booking ? { day: dayOf(person.last_booking), purpose: person.last_booking.purpose } : lastBooked ? { day: dayOf(lastBooked), purpose: lastBooked.reason ?? undefined } : null;
  const detail = [relationLabel(person.relation), ageBandLabel(person.age_band)].filter(Boolean).join(" · ");

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.put<CallerProfile>(url, { name: name.trim(), relation: relation || null, age_band: band || null });
      toast.success("Saved. The next call uses this.");
      setEditing(false);
      await onChanged(updated);
    } catch (e) {
      setError(errMessage(e, "Could not save. Try again."));
    } finally {
      setSaving(false);
    }
  };
  const remove = async () => {
    try {
      const updated = await api.del<CallerProfile>(url);
      toast.success(`Forgotten. Nothing about ${firstName(person.name)} is kept.`);
      setAsking(false);
      await onChanged(updated);
    } catch (e) {
      toast.error(errMessage(e, "Could not forget this person. Try again."));
    }
  };

  return (
    <li className="rounded-lg border border-border bg-bg-surface p-3" data-testid="family-person">
      {editing ? (
        <form aria-label={`Edit ${person.name}`} className="space-y-2" onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <div className="grid gap-2 sm:grid-cols-[1fr_8rem_7rem]">
            <input aria-label="Name" className={input} value={name} maxLength={60} autoComplete="off" onChange={(e) => setName(e.target.value)} />
            <select aria-label="Relation to the caller" className={input} value={relation} onChange={(e) => setRelation(e.target.value)}>
              <option value="">Relation</option>
              {RELATIONS.map((r) => <option key={r} value={r}>{relationLabel(r)}</option>)}
            </select>
            <select aria-label="Age band" className={input} value={band} onChange={(e) => setBand(e.target.value)}>
              {BANDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
          {error && <p id={id} role="alert" className="text-sm text-accent-error">{error}</p>}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" loading={saving} disabled={!name.trim()}>Save</Button>
            <Button type="button" size="sm" variant="ghost" disabled={saving} onClick={() => { setEditing(false); setName(person.name); setRelation(person.relation ?? ""); setBand(person.age_band ?? ""); setError(null); }}>Cancel</Button>
          </div>
        </form>
      ) : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="break-words text-sm font-semibold text-text-primary">{person.name}</p>
              {detail && <p className="text-xs text-text-secondary">{detail}</p>}
            </div>
            <div className="flex shrink-0 gap-1">
              <Button size="sm" variant="ghost" aria-label={`Edit ${person.name}`} leftIcon={<Pencil className="size-3.5" />} onClick={() => setEditing(true)}>Edit</Button>
              <Button size="sm" variant="ghost" aria-label={`Forget ${person.name}`} leftIcon={<Trash2 className="size-3.5" />} onClick={() => setAsking(true)}>Remove</Button>
            </div>
          </div>
          <FactChips className="mt-2" facts={person.facts} />
          <dl className="mt-2 space-y-0.5 text-sm">
            <div className="flex gap-2"><dt className="w-24 shrink-0 text-text-secondary">Last booking</dt><dd className="min-w-0 break-words text-text-primary">{last?.day ? `${last.day}${last.purpose ? ` · ${last.purpose}` : ""}` : "None yet"}</dd></div>
            <div className="flex gap-2"><dt className="w-24 shrink-0 text-text-secondary">Next booking</dt><dd className="min-w-0 break-words text-text-primary">{next ? `${dayOf(next) ?? "Time not set"}${next.reason ? ` · ${next.reason}` : ""}` : "None"}</dd></div>
          </dl>
        </>
      )}
      <ConfirmDialog
        open={asking}
        title={`Forget ${person.name}?`}
        description={`Their name, relation, usual details and last booking are deleted now. The caller and the rest of the family stay, and their bookings stay on the schedule.`}
        confirmLabel={`Forget ${firstName(person.name)}`}
        keepLabel="Keep"
        onCancel={() => setAsking(false)}
        onConfirm={remove}
      />
    </li>
  );
}

/**
 * "One number, the whole family": the people a number books for (a son for his mother, a mother for her children), one card each
 * with the relation to the caller, age band, usual details, last and next booking, and edit and forget per person
 * (PUT and DELETE /api/callers/{id}/people/{name}; backend/services/caller_profiles.py). The bookings come from
 * /api/bookings?caller_ref= matched by name.
 */
export function FamilyCard({ profile, onChanged, className }: { profile: CallerProfile; onChanged: (updated: CallerProfile) => void | Promise<unknown>; className?: string }) {
  const ref = profile.caller_ref ?? profile.id;
  const { data } = useSWR<{ bookings?: BookingRow[] }>(`/api/bookings?caller_ref=${encodeURIComponent(ref)}`, (p: string) => api.get<{ bookings?: BookingRow[] }>(p),
    { revalidateOnFocus: false, shouldRetryOnError: false });
  const bookings = Array.isArray(data?.bookings) ? data.bookings : [];
  const people = profile.people ?? [];
  const lastKey = (profile.last_person ?? "").toLowerCase();
  const ordered = [...people].sort((a, b) => Number(b.name.toLowerCase() === lastKey) - Number(a.name.toLowerCase() === lastKey));
  return (
    <section aria-labelledby="family-heading" className={cn("rounded-lg border border-border bg-bg-elevated p-3", className)} data-testid="family-card">
      <h3 id="family-heading" className="font-display text-lg text-text-primary">One number, the whole family</h3>
      <p className="mt-0.5 text-sm text-text-secondary">
        {`Vakya remembers who this number books for, so a call for a parent or a child starts with “Is this for ${people[0] ? firstName(people[0].name) : "them"} again, or for someone else?”`}
      </p>
      {ordered.length === 0 ? (
        <p className="mt-3 text-sm text-text-secondary">Nobody yet. When this number books for someone, they are added after the call.</p>
      ) : (
        <ul className="mt-3 space-y-2" aria-label="People on this number">
          {ordered.map((p) => <PersonCard key={p.name} profile={profile} person={p} bookings={bookings} onChanged={onChanged} />)}
        </ul>
      )}
    </section>
  );
}
