"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, BedDouble, CalendarClock, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";
import { Button } from "@/components/ui";
import { PageShell } from "@/components/ui/PageHeader";
import { useAgents } from "@/hooks/useApi";
import { cn } from "@/lib/utils";
import {
  DAY_KEYS,
  DAY_LONG,
  HOURS_PRESETS,
  SLOT_CHOICES,
  createSchedule,
  dayLabel,
  deleteSchedule,
  draftToInput,
  defaultDraft,
  hasBookingsError,
  issuesFor,
  plainError,
  readPref,
  scheduleToDraft,
  updateSchedule,
  validateDraft,
  type DayKey,
  type Range,
  type Schedule,
  type ScheduleDraft,
  type ScheduleKind,
} from "@/lib/schedules";
import { DayChips } from "./form/DayChips";
import { chipClass } from "./form/chipStyle";
import { DescribeBox, type Changed } from "./form/DescribeBox";
import { Chips, Field, Step, Stepper, Toggle, inputCls } from "./form/Fields";
import { RangesEditor } from "./form/RangesEditor";
import { SchedulePreview } from "./form/SchedulePreview";
import { TimeField } from "./form/TimeField";
import { FrontDeskSelect } from "./FrontDeskSelect";
import { LinkButton } from "./LinkButton";
import { useMediaQuery } from "./useMediaQuery";

const HORIZONS = [
  { value: 7, label: "1 week" },
  { value: 14, label: "2 weeks" },
  { value: 30, label: "30 days" },
  { value: 60, label: "2 months" },
  { value: 90, label: "3 months" },
  { value: 180, label: "6 months" },
];
const NOTICES = [
  { value: 0, label: "No notice needed" },
  { value: 30, label: "30 minutes" },
  { value: 60, label: "1 hour" },
  { value: 120, label: "2 hours" },
  { value: 240, label: "4 hours" },
  { value: 1440, label: "1 day" },
];
const withCurrent = (opts: { value: number; label: string }[], v: number) => (opts.some((o) => o.value === v) ? opts : [...opts, { value: v, label: `${v} ${opts === HORIZONS ? "days" : "minutes"}` }].sort((a, b) => a.value - b.value));

const sameRanges = (a: { start: string; end: string }[], b: { start: string; end: string }[]) => a.length === b.length && a.every((r, i) => r.start === b[i].start && r.end === b[i].end);

function KindCard({ kind, current, onPick, icon, title, text }: { kind: ScheduleKind; current: ScheduleKind; onPick: (k: ScheduleKind) => void; icon: React.ReactNode; title: string; text: string }) {
  const on = kind === current;
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      onClick={() => onPick(kind)}
      className={cn(
        "flex min-h-24 items-start gap-3 rounded-xl border p-4 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary",
        on ? "border-accent-primary bg-violet-200/50 ring-1 ring-accent-primary" : "border-border bg-bg-surface hover:bg-bg-base"
      )}
    >
      <span aria-hidden className={cn("grid size-10 shrink-0 place-items-center rounded-lg", on ? "bg-brand text-[var(--color-on-accent)]" : "bg-bg-elevated text-text-secondary")}>{icon}</span>
      <span>
        <span className="block text-[15px] font-semibold text-text-primary">{title}</span>
        <span className="mt-0.5 block text-sm text-text-secondary">{text}</span>
      </span>
    </button>
  );
}

/**
 * Make or change a schedule, in plain steps with a live preview beside it. Used by /desk/bookings/new and
 * /desk/bookings/[id]/edit (`schedule` is set on edit).
 */
export function ScheduleForm({ schedule }: { schedule?: Schedule }) {
  const editing = !!schedule;
  const router = useRouter();
  const params = useSearchParams();
  const { mutate } = useSWRConfig();
  const agents = useAgents();
  const wide = useMediaQuery("(min-width: 1024px)");

  const [draft, setDraft] = useState<ScheduleDraft>(() => (schedule ? scheduleToDraft(schedule) : defaultDraft(null)));
  const [changed, setChanged] = useState<Set<Changed>>(new Set());
  const [tried, setTried] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [holiday, setHoliday] = useState("");
  const [customSlot, setCustomSlot] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<"ask" | "force" | null>(null);
  const [deleting, setDeleting] = useState(false);

  const list = agents.data ?? [];
  const wanted = params.get("agent") ?? readPref();
  const defaultAgent = list.find((a) => a.id === wanted)?.id ?? list[0]?.id ?? null;
  const agentId = draft.agent_id ?? defaultAgent;
  const nights = draft.kind === "nights";
  const issues = useMemo(() => validateDraft(draft), [draft]);

  const edit = (patch: Partial<ScheduleDraft>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setChanged(new Set());
  };
  const setShared = (shared: Range[]) => edit({ shared });
  const setDay = (day: DayKey, ranges: Range[]) => edit({ perDay: { ...draft.perDay, [day]: ranges } });

  function setSame(on: boolean) {
    if (on) {
      const first = draft.openDays[0];
      edit({ sameHours: true, shared: first && draft.perDay[first]?.length ? draft.perDay[first] : draft.shared });
    } else {
      edit({ sameHours: false, perDay: Object.fromEntries(DAY_KEYS.map((d) => [d, draft.openDays.includes(d) || !draft.perDay[d]?.length ? draft.shared.map((r) => ({ ...r })) : draft.perDay[d]])) as ScheduleDraft["perDay"] });
    }
  }
  function pickKind(kind: ScheduleKind) {
    if (kind === draft.kind) return;
    edit({
      kind,
      ...(kind === "nights" ? { capacity: draft.capacity === 1 ? 5 : draft.capacity, openDays: [...DAY_KEYS], horizon_days: Math.max(draft.horizon_days, 90) } : { capacity: draft.capacity > 20 ? 1 : draft.capacity }),
    });
  }
  function addHoliday() {
    if (!holiday || draft.closed_dates.includes(holiday)) return;
    edit({ closed_dates: [...draft.closed_dates, holiday].sort() });
    setHoliday("");
  }

  const blocking = issues;
  async function save() {
    setTried(true);
    if (blocking.length) {
      toast.error("A few things need fixing first. They are marked in red.");
      document.querySelector<HTMLElement>("[role=alert]")?.scrollIntoView?.({ block: "center", behavior: "smooth" });
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const input = draftToInput({ ...draft, agent_id: agentId });
      const saved = editing && schedule ? await updateSchedule(schedule.id, input) : await createSchedule(input);
      await mutate((k) => typeof k === "string" && k.startsWith("/api/schedules"));
      toast.success(editing ? "Schedule saved" : "Schedule created");
      router.push(`/desk/bookings/${encodeURIComponent(saved.id)}`);
    } catch (e) {
      setSaveError(plainError(e, "Could not save the schedule. Nothing was changed, so you can try again."));
      setSaving(false);
    }
  }

  async function remove(force: boolean) {
    if (!schedule) return;
    setDeleting(true);
    try {
      await deleteSchedule(schedule.id, force);
      await mutate((k) => typeof k === "string" && k.startsWith("/api/schedules"));
      toast.success("Schedule deleted");
      router.push("/desk/schedules");
    } catch (e) {
      if (hasBookingsError(e) && !force) setConfirmDelete("force");
      else toast.error(plainError(e, "Could not delete the schedule."));
      setDeleting(false);
    }
  }

  const nameError = tried ? issuesFor(issues, "name")[0]?.message : undefined;
  const preview = <SchedulePreview draft={draft} issues={issues} className={wide ? "sticky top-6" : "rounded-none border-0 bg-transparent p-0"} />;
  const sharedIssues = issuesFor(issues, "hours");
  const slotIssues = issuesFor(issues, "slots");

  return (
    <PageShell width="wide" className="space-y-6">
      <header className="space-y-3">
        <Link href={editing && schedule ? `/desk/bookings/${encodeURIComponent(schedule.id)}` : "/desk/bookings"} className="inline-flex min-h-10 items-center gap-1.5 text-sm font-medium text-text-secondary hover:text-text-primary">
          <ArrowLeft aria-hidden className="size-4" /> {editing ? "Back to the calendar" : "All schedules"}
        </Link>
        <h1 className="font-display text-[clamp(1.75rem,2.6vw,2.5rem)] font-normal leading-[1.08] tracking-[-0.005em] text-text-primary">
          {editing ? `Edit ${schedule?.name ?? "schedule"}` : "New schedule"}
        </h1>
        <p className="max-w-prose text-[15px] text-text-secondary">Say when this front desk can be booked. The preview updates as you go, and the front desk only offers times that fit it.</p>
      </header>

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <form
          className="min-w-0 space-y-5"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <Step id="step-what" title="What is it?">
            <Field label="Name" htmlFor="sched-name" error={nameError}>
              <input id="sched-name" value={draft.name} onChange={(e) => edit({ name: e.target.value })} placeholder="Appointments" autoComplete="off" aria-invalid={!!nameError || undefined} className={cn(inputCls, nameError && "border-accent-error")} />
            </Field>
            <Field label="Front desk" hint="The front desk that books these times when it answers a call.">
              {list.length === 0 && !agents.isLoading ? (
                <p className="text-sm text-text-secondary">
                  You have no front desk yet. <Link href="/desk/receptionist" className="font-medium text-accent-primary hover:underline">Set one up first</Link>, then come back.
                </p>
              ) : (
                <FrontDeskSelect agents={list} value={agentId ?? ""} onChange={(v) => edit({ agent_id: v })} allowAll={false} size="field" />
              )}
            </Field>
            <div className="space-y-1.5">
              <p id="kind-label" className="text-sm font-semibold text-text-primary">What do callers book?</p>
              <div role="radiogroup" aria-labelledby="kind-label" className="grid gap-3 sm:grid-cols-2">
                <KindCard kind="slots" current={draft.kind} onPick={pickKind} icon={<CalendarClock className="size-5" />} title="Time slots" text="Appointments, consultations, tables" />
                <KindCard kind="nights" current={draft.kind} onPick={pickKind} icon={<BedDouble className="size-5" />} title="Nightly stays" text="Rooms, beds" />
              </div>
            </div>
          </Step>

          <Step id="step-days" title={nights ? "Which nights can be booked?" : "Which days are you open?"} hint="Tap a day to open or close it, or pick a quick choice." changed={changed.has("days")}>
            <DayChips open={draft.openDays} onChange={(openDays) => edit({ openDays })} nights={nights} />
          </Step>

          {nights ? (
            <Step id="step-hours" title="Check-in and check-out" hint="The times guests arrive from and leave by.">
              <div className="flex flex-wrap items-end gap-6">
                <Field label="Check-in from">
                  <TimeField label="Check-in from" value={draft.check_in} onChange={(v) => edit({ check_in: v })} />
                </Field>
                <Field label="Check-out by">
                  <TimeField label="Check-out by" value={draft.check_out} onChange={(v) => edit({ check_out: v })} />
                </Field>
              </div>
              {tried && issuesFor(issues, "stay").map((i) => <p key={i.message} role="alert" className="text-sm text-accent-error">{i.message}</p>)}
            </Step>
          ) : (
            <Step id="step-hours" title="What hours?" hint="Add a break if you close for lunch. Times can be typed, like 5:30 pm, or picked from the list." changed={changed.has("hours")}>
              <Toggle checked={draft.sameHours} onChange={setSame} label="Same hours every open day" />
              {draft.openDays.length === 0 ? (
                <p className="rounded-lg bg-bg-base px-3 py-2 text-sm text-text-secondary">Every day is closed, so callers will not be offered any time. Open a day above to set its hours.</p>
              ) : draft.sameHours ? (
                <div className="space-y-4">
                  <div className="flex flex-wrap gap-2" role="group" aria-label="Quick hours">
                    {HOURS_PRESETS.map((p) => {
                      const on = sameRanges(draft.shared, p.ranges);
                      return (
                        <button key={p.id} type="button" aria-pressed={on} onClick={() => setShared(p.ranges.map((r) => ({ ...r })))} className={chipClass(on, "min-h-9 rounded-full px-3.5 text-[13px]", !on && "font-medium")}>
                          {p.label}
                        </button>
                      );
                    })}
                  </div>
                  <RangesEditor ranges={draft.shared} onChange={setShared} issues={sharedIssues} />
                </div>
              ) : (
                <ul className="divide-y divide-border rounded-xl border border-border">
                  {DAY_KEYS.map((d) => {
                    const open = draft.openDays.includes(d);
                    return (
                      <li key={d} className="flex flex-col gap-3 p-3 sm:flex-row sm:items-start sm:gap-4">
                        <p className="w-24 shrink-0 pt-2 text-sm font-semibold text-text-primary">{DAY_LONG[d]}</p>
                        {open ? (
                          <div className="min-w-0 flex-1 space-y-2">
                            <RangesEditor compact label={DAY_LONG[d]} ranges={draft.perDay[d] ?? []} onChange={(r) => setDay(d, r)} issues={issuesFor(issues, `hours:${d}`)} />
                            <Button type="button" variant="ghost" size="sm" onClick={() => edit({ perDay: Object.fromEntries(DAY_KEYS.map((k) => [k, (draft.perDay[d] ?? []).map((r) => ({ ...r }))])) as ScheduleDraft["perDay"] })}>
                              Copy to all days
                            </Button>
                          </div>
                        ) : (
                          <p className="pt-2 text-sm text-text-secondary">Closed</p>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
              <DescribeBox
                draft={draft}
                onApply={(next, ch) => {
                  setDraft(next);
                  setChanged(new Set(ch));
                }}
                onUndo={(prev) => {
                  setDraft(prev);
                  setChanged(new Set());
                }}
              />
            </Step>
          )}

          <Step id="step-slots" title={nights ? "Rooms and rules" : "How are slots booked?"} changed={changed.has("slots") || changed.has("holidays")}>
            {!nights && (
              <>
                <div className="space-y-2">
                  <p id="slot-label" className="text-sm font-semibold text-text-primary">How long is each slot?</p>
                  <div className="flex flex-wrap items-center gap-2">
                    <Chips
                      label="Slot length in minutes"
                      options={[...SLOT_CHOICES.map((n) => ({ value: n as number, label: `${n} min` })), { value: -1, label: "Custom" }]}
                      value={customSlot || !(SLOT_CHOICES as readonly number[]).includes(draft.slot_minutes) ? -1 : draft.slot_minutes}
                      onChange={(v) => {
                        if (v === -1) setCustomSlot(true);
                        else {
                          setCustomSlot(false);
                          edit({ slot_minutes: v });
                        }
                      }}
                    />
                  </div>
                  {(customSlot || !(SLOT_CHOICES as readonly number[]).includes(draft.slot_minutes)) && (
                    <Stepper label="Custom slot length" value={draft.slot_minutes} min={5} max={240} suffix="minutes" onChange={(n) => edit({ slot_minutes: n })} />
                  )}
                  {slotIssues.map((i) => <p key={i.message} role="alert" className="text-sm text-accent-error">{i.message}</p>)}
                </div>
                <div className="space-y-2">
                  <p className="text-sm font-semibold text-text-primary">Gap between bookings</p>
                  <Stepper label="Gap between bookings" value={draft.buffer_minutes} min={0} max={120} suffix={draft.buffer_minutes === 0 ? "minutes, none" : "minutes"} onChange={(n) => edit({ buffer_minutes: n })} />
                </div>
              </>
            )}
            <div className="space-y-2">
              <p className="text-sm font-semibold text-text-primary">{nights ? "Rooms" : "How many at the same time?"}</p>
              {!nights && <p className="text-sm text-text-secondary">One for a single doctor, chair or counter. More for tables or several staff.</p>}
              <Stepper label={nights ? "Rooms" : "How many at the same time"} value={draft.capacity} min={1} max={500} suffix={nights ? (draft.capacity === 1 ? "room" : "rooms") : undefined} onChange={(n) => edit({ capacity: n })} />
            </div>
            <div className="grid gap-5 sm:grid-cols-2">
              <Field label="How far ahead can callers book?" htmlFor="sched-horizon">
                <select id="sched-horizon" value={draft.horizon_days} onChange={(e) => edit({ horizon_days: Number(e.target.value) })} className={inputCls}>
                  {withCurrent(HORIZONS, draft.horizon_days).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </Field>
              <Field label="Minimum notice" htmlFor="sched-notice">
                <select id="sched-notice" value={draft.min_notice_minutes} onChange={(e) => edit({ min_notice_minutes: Number(e.target.value) })} className={inputCls}>
                  {withCurrent(NOTICES, draft.min_notice_minutes).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </Field>
            </div>
            <div className="space-y-2">
              <label htmlFor="sched-holiday" className="block text-sm font-semibold text-text-primary">Holidays</label>
              <p className="text-sm text-text-secondary">Days you are closed even though the weekday is open.</p>
              <div className="flex flex-wrap items-center gap-2">
                <input id="sched-holiday" type="date" value={holiday} onChange={(e) => setHoliday(e.target.value)} className={cn(inputCls, "w-auto")} />
                <Button type="button" variant="outline" onClick={addHoliday} disabled={!holiday} disabledReason="Pick a date first">Add holiday</Button>
              </div>
              {draft.closed_dates.length > 0 && (
                <ul className="flex flex-wrap gap-2">
                  {draft.closed_dates.map((d) => (
                    <li key={d} className="inline-flex items-center gap-1 rounded-full border border-border bg-bg-base py-1 pl-3 pr-1 text-sm text-text-primary">
                      {dayLabel(d)} {d.slice(0, 4)}
                      <button type="button" aria-label={`Remove holiday ${dayLabel(d)}`} onClick={() => edit({ closed_dates: draft.closed_dates.filter((x) => x !== d) })} className="grid size-7 place-items-center rounded-full text-text-secondary hover:bg-bg-surface">
                        <X aria-hidden className="size-3.5" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Step>

          <Step id="step-review" title="Review and save" hint="This is what callers will be told.">
            {!wide && preview}
            {wide && <p className="text-sm text-text-secondary">The preview on the right is the sentence callers will hear and the week as it will be offered.</p>}
            {tried && issues.length > 0 && (
              <ul className="space-y-1 rounded-lg border border-accent-error/30 bg-error-tint p-3 text-sm text-accent-error" aria-label="Things to fix">
                {issues.map((i, k) => <li key={k}>{i.message}</li>)}
              </ul>
            )}
            {saveError && <p role="alert" className="text-sm text-accent-error">{saveError}</p>}
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" loading={saving}>{editing ? "Save changes" : "Create schedule"}</Button>
              <LinkButton href={editing && schedule ? `/desk/bookings/${encodeURIComponent(schedule.id)}` : "/desk/bookings"} variant="ghost">Cancel</LinkButton>
            </div>
          </Step>

          {editing && (
            <section aria-labelledby="danger-title" className="rounded-2xl border border-border bg-bg-surface p-5 md:p-6">
              <h2 id="danger-title" className="font-display text-[1.375rem] leading-tight text-text-primary">Delete this schedule</h2>
              {confirmDelete === null && (
                <>
                  <p className="mt-1 text-sm text-text-secondary">Callers will no longer be offered these times.</p>
                  <Button type="button" variant="outline" className="mt-4" leftIcon={<Trash2 className="size-4" />} onClick={() => setConfirmDelete("ask")}>Delete schedule</Button>
                </>
              )}
              {confirmDelete === "ask" && (
                <div className="mt-3 space-y-3" role="alertdialog" aria-label="Confirm delete">
                  <p className="text-sm text-text-primary">Delete {schedule?.name}? This cannot be undone.</p>
                  <div className="flex gap-2">
                    <Button type="button" loading={deleting} onClick={() => void remove(false)}>Yes, delete it</Button>
                    <Button type="button" variant="ghost" onClick={() => setConfirmDelete(null)}>Keep it</Button>
                  </div>
                </div>
              )}
              {confirmDelete === "force" && (
                <div className="mt-3 space-y-3" role="alertdialog" aria-label="Confirm delete with bookings">
                  <p className="text-sm text-text-primary">People are still booked on this schedule. If you delete it, those bookings go too, and nobody is told. Delete it anyway?</p>
                  <div className="flex gap-2">
                    <Button type="button" loading={deleting} onClick={() => void remove(true)}>Delete it and its bookings</Button>
                    <Button type="button" variant="ghost" onClick={() => setConfirmDelete(null)}>Keep it</Button>
                  </div>
                </div>
              )}
            </section>
          )}
        </form>

        {wide && <aside className="min-w-0 self-stretch">{preview}</aside>}
      </div>
    </PageShell>
  );
}
