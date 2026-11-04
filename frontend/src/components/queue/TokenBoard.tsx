"use client";

import { useState, type FormEvent } from "react";
import { ArrowRight, PhoneCall, UserPlus } from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";
import { Badge, Button } from "@/components/ui";
import { plainError } from "@/lib/schedules";
import { cn } from "@/lib/utils";
import {
  addWalkIn,
  bookedByWords,
  callError,
  clock,
  notifyPatient,
  setNowServing,
  statusWords,
  todayKey,
  type QueueToken,
  type QueueToday,
} from "./queue";

/**
 * Today's tokens: who is with the doctor now (and a button for the next one), everyone in the queue with when they are
 * expected, a walk-in form, and the patients whose time moved by 15 minutes or more, each with "Call to let them know".
 */
export function TokenBoard({ scheduleId, queue }: { scheduleId: string; queue: QueueToday }) {
  const { mutate } = useSWRConfig();
  const [busy, setBusy] = useState<string | null>(null);
  const [calling, setCalling] = useState<string | null>(null);
  const [called, setCalled] = useState<Record<string, true>>({});
  const [walkIn, setWalkIn] = useState(false);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");

  const serving = queue.now_serving;
  const current = queue.tokens.find((t) => t.token === serving);
  const hasNext = queue.tokens.some((t) => t.token > (serving ?? 0));

  async function serve(token: number | "next", what: string) {
    setBusy(what);
    try {
      await mutate(todayKey(scheduleId), await setNowServing(scheduleId, token), { revalidate: false });
    } catch (e) {
      toast.error(plainError(e, "Could not change the token. Nothing was changed."));
    } finally {
      setBusy(null);
    }
  }

  async function call(t: QueueToken) {
    setCalling(t.booking_id);
    try {
      await notifyPatient(scheduleId, t.booking_id);
      setCalled((c) => ({ ...c, [t.booking_id]: true }));
      toast.success(`Calling ${t.name}. The front desk will tell them the new time.`);
    } catch (e) {
      toast.error(callError(e));
    } finally {
      setCalling(null);
    }
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    setBusy("walk-in");
    try {
      const res = await addWalkIn(scheduleId, name, phone);
      await mutate(todayKey(scheduleId), res.today, { revalidate: false });
      toast.success(`Added ${name.trim()} as token ${res.booking.token ?? "next"}`);
      setName("");
      setPhone("");
      setWalkIn(false);
    } catch (err) {
      toast.error(plainError(err, "Could not add the walk-in."));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-bg-base px-4 py-3">
        <p className="text-[15px] text-text-primary" aria-live="polite">
          {serving ? (
            <>
              Now seeing <span className="font-display text-xl tabular-nums">token {serving}</span>
              {current ? <span className="text-text-secondary"> · {current.name}</span> : null}
            </>
          ) : (
            <span className="text-text-secondary">Nobody has been called in yet</span>
          )}
        </p>
        <Button size="md" disabled={!hasNext || busy === "next"} disabledReason="That was the last token today" onClick={() => void serve("next", "next")} rightIcon={<ArrowRight aria-hidden className="size-4" />}>
          {serving ? "Next" : "Call the first token"}
        </Button>
      </div>

      {queue.tokens.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border p-4 text-sm text-text-secondary">No appointments today yet. A walk-in gets the first free time.</p>
      ) : (
        <ol aria-label="Today's tokens" className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-bg-surface">
          {queue.tokens.map((t) => {
            const by = bookedByWords(t);
            return (
              <li key={t.booking_id} className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5", t.status === "seen" && "opacity-60")}>
                <span className="grid h-9 min-w-9 place-items-center rounded-lg bg-bg-elevated px-2 font-display text-lg tabular-nums text-text-primary" aria-label={`Token ${t.token}`}>
                  {t.token}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[15px] font-medium text-text-primary">
                    {t.name}
                    {t.source === "walk_in" && <span className="ml-2 align-middle"><Badge>Walk-in</Badge></span>}
                  </p>
                  <p className="text-sm text-text-secondary">
                    {clock(t.slot_start)} slot{by ? ` · ${by}` : ""}
                  </p>
                </div>
                <p className={cn("text-sm", t.status === "now" ? "font-semibold text-text-primary" : "text-text-secondary")}>{statusWords(t)}</p>
                {t.status === "waiting" && (
                  <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void serve(t.token, `t${t.token}`)} aria-label={`Mark token ${t.token}, ${t.name}, as now seeing`}>
                    Now seeing
                  </Button>
                )}
              </li>
            );
          })}
        </ol>
      )}

      {queue.minutes_late > 0 && (
        <section aria-labelledby={`moved-${scheduleId}`} className="space-y-2">
          <h4 id={`moved-${scheduleId}`} className="text-sm font-semibold text-text-primary">Moved by 15 minutes or more</h4>
          {queue.affected.length === 0 ? (
            <p className="text-sm text-text-secondary">Nobody&apos;s time has moved that much yet.</p>
          ) : (
            <>
              <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-bg-surface">
                {queue.affected.map((t) => (
                  <li key={t.booking_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[15px] font-medium text-text-primary">Token {t.token} · {t.name}</p>
                      <p className="text-sm text-text-secondary">
                        {clock(t.slot_start)} is now around <span className="font-medium text-text-primary">{clock(t.suggested_time)}</span>
                      </p>
                    </div>
                    {called[t.booking_id] ? (
                      <Badge tone="success">Calling</Badge>
                    ) : (
                      <Button
                        size="sm"
                        variant="outline"
                        loading={calling === t.booking_id}
                        disabled={!t.can_call || calling !== null}
                        disabledReason="This booking has no phone number"
                        onClick={() => void call(t)}
                        leftIcon={<PhoneCall aria-hidden className="size-4" />}
                        aria-label={`Call ${t.name} to let them know`}
                      >
                        Call to let them know
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
              {!queue.calling.ready && <p className="text-sm text-text-secondary">Calling is not set up yet, so the buttons above will not place a call. Tell these patients yourself.</p>}
            </>
          )}
        </section>
      )}

      <div>
        {!walkIn ? (
          <Button size="sm" variant="ghost" onClick={() => setWalkIn(true)} leftIcon={<UserPlus aria-hidden className="size-4" />}>
            Add a walk-in
          </Button>
        ) : (
          <form onSubmit={(e) => void add(e)} className="flex flex-wrap items-end gap-2" aria-label="Add a walk-in">
            <label className="text-sm text-text-secondary">
              Name
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoComplete="off"
                className="mt-1 block h-10 w-44 rounded-lg border border-border bg-bg-surface px-3 text-sm text-text-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary"
              />
            </label>
            <label className="text-sm text-text-secondary">
              Phone (optional)
              <input
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                inputMode="tel"
                autoComplete="off"
                className="mt-1 block h-10 w-40 rounded-lg border border-border bg-bg-surface px-3 text-sm text-text-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary"
              />
            </label>
            <Button type="submit" size="md" loading={busy === "walk-in"} disabled={name.trim().length < 2} disabledReason="Type the patient's name">
              Add walk-in
            </Button>
            <Button type="button" size="md" variant="ghost" onClick={() => setWalkIn(false)}>
              Cancel
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
