"use client";

import { useEffect, useId, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { Ban, CalendarPlus, Undo2, X } from "lucide-react";
import { Badge, Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import { SOURCE_LABEL, SourceIcon } from "./bookingWho";
import { dayLabelLong, isoDate, plainError, slotSpan, timeWords, isoHHMM, type Schedule, type Slot } from "@/lib/schedules";

export interface PopoverTarget {
  slot: Slot;
  el: HTMLElement;
  following: Slot[];
}

const inputCls =
  "h-10 w-full rounded-lg border border-border bg-bg-surface px-3 text-sm text-text-primary placeholder:text-text-muted focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary";

/**
 * What you can do with one slot or night: mark it busy (optionally for longer), book someone, or free it again.
 * A card next to the slot on a computer, a bottom sheet on a phone. Escape or a click elsewhere closes it.
 */
export function SlotPopover({
  schedule,
  target,
  onClose,
  onBlock,
  onFree,
  onBook,
  onOpenBooking,
}: {
  schedule: Schedule;
  target: PopoverTarget;
  onClose: () => void;
  onBlock: (endIso: string) => void;
  onFree: (blockId: string) => void;
  onBook: (input: { name: string; phone: string; nights: number }) => Promise<void>;
  /** Open one booking's drawer (who it is for, the proof of how it was made, actions). */
  onOpenBooking?: (bookingId: string) => void;
}) {
  const { slot, el, following } = target;
  const nights = schedule.kind === "nights";
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const [mode, setMode] = useState<"menu" | "book">("menu");
  const [until, setUntil] = useState(slot.end);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [count, setCount] = useState(1);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rect] = useState(() => el.getBoundingClientRect());
  const [vw] = useState(() => window.innerWidth);
  const [vh] = useState(() => window.innerHeight);
  const sheet = vw < 640;

  useEffect(() => {
    const away = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node) && !el.contains(e.target as Node)) onClose();
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        el.focus();
      }
    };
    const scroll = (e: Event) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", key);
    if (!sheet) window.addEventListener("scroll", scroll, true);
    ref.current?.querySelector<HTMLElement>("button, input")?.focus();
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", key);
      window.removeEventListener("scroll", scroll, true);
    };
  }, [el, onClose, sheet]);

  const width = 288;
  const left = Math.max(8, Math.min(rect.left, vw - width - 8));
  const below = rect.bottom + 8 + 260 < vh || rect.top < 280;
  const style: CSSProperties = sheet ? {} : { left, width, ...(below ? { top: Math.min(rect.bottom + 6, vh - 120) } : { bottom: vh - rect.top + 6 }) };

  const dayText = dayLabelLong(isoDate(slot.start));
  const spanText = nights ? `Check-in ${timeWords(isoHHMM(slot.start))}, check-out ${timeWords(isoHHMM(slot.end))} next day` : slotSpan(slot.start, slot.end);
  const tone = slot.status === "free" ? "success" : slot.status === "blocked" ? "neutral" : slot.status === "booked" ? "primary" : "warning";
  const toneText =
    slot.status === "free" ? (nights ? `${slot.remaining} rooms left` : "Free")
    : slot.status === "blocked" ? "Busy"
    : slot.status === "booked" ? `${slot.remaining} of ${schedule.capacity} left`
    : slot.status === "full" ? "Full"
    : "Past";

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError("Type the name of the person you are booking.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onBook({ name: name.trim(), phone: phone.trim(), nights: count });
    } catch (err) {
      setError(plainError(err, "Could not book that. Try again."));
      setSaving(false);
    }
  }

  return (
    <>
      {sheet && <div aria-hidden className="fixed inset-0 z-40 bg-black/30" />}
      <div
        ref={ref}
        role="dialog"
        aria-modal="false"
        aria-labelledby={titleId}
        style={style}
        className={cn(
          "fixed z-50 border border-border bg-bg-surface p-4 shadow-[0_18px_40px_-16px_rgba(0,0,0,0.35)]",
          sheet ? "inset-x-0 bottom-0 max-h-[85vh] overflow-auto rounded-t-2xl pb-6" : "rounded-xl"
        )}
      >
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <p id={titleId} className="text-[15px] font-semibold leading-snug text-text-primary">
              {dayText}
              <span className="block text-sm font-normal text-text-secondary">{spanText}</span>
            </p>
            <div className="mt-1.5"><Badge tone={tone}>{toneText}</Badge></div>
          </div>
          <button type="button" aria-label="Close" onClick={onClose} className="-m-1.5 grid size-10 place-items-center rounded-lg text-text-secondary hover:bg-bg-base">
            <X aria-hidden className="size-4" />
          </button>
        </div>

        {slot.bookings.length > 0 && (
          <ul className="mt-3 space-y-1.5 text-sm text-text-primary" aria-label="Who is booked">
            {slot.bookings.map((b) => (
              <li key={b.id}>
                <button
                  type="button"
                  onClick={() => onOpenBooking?.(b.id)}
                  aria-label={`Open booking ${b.id} for ${b.name ?? "a caller"}`}
                  className="flex w-full items-center gap-2.5 rounded-lg bg-bg-base px-2.5 py-2 text-left hover:bg-bg-elevated focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary"
                >
                  <span aria-hidden title={SOURCE_LABEL[b.source ?? "phone"]} className="text-text-secondary"><SourceIcon source={b.source ?? "phone"} className="size-4" /></span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[15px] font-semibold">{b.name ?? "A caller"}</span>
                    <span className="block truncate text-xs text-text-secondary">{b.phone_masked ? `${b.phone_masked} · ` : ""}{b.id}{b.nights ? ` · ${b.nights} night${b.nights === 1 ? "" : "s"}` : ""}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {mode === "menu" && (
          <div className="mt-4 space-y-2">
            {slot.status === "free" && (
              <>
                {!nights && following.length > 0 && (
                  <label className="block text-sm text-text-secondary">
                    Busy until
                    <select value={until} onChange={(e) => setUntil(e.target.value)} className={cn(inputCls, "mt-1")}>
                      <option value={slot.end}>{timeWords(isoHHMM(slot.end))} (this slot only)</option>
                      {following.map((f) => (
                        <option key={f.start} value={f.end}>{timeWords(isoHHMM(f.end))}</option>
                      ))}
                    </select>
                  </label>
                )}
                <Button className="w-full" variant="outline" leftIcon={<Ban className="size-4" />} onClick={() => onBlock(nights ? slot.end : until)}>
                  {nights ? "Block this night" : "Mark busy"}
                </Button>
              </>
            )}
            {(slot.status === "free" || slot.status === "booked") && (
              <Button className="w-full" leftIcon={<CalendarPlus className="size-4" />} onClick={() => setMode("book")}>
                Book someone
              </Button>
            )}
            {slot.status === "blocked" && slot.block_id && (
              <Button className="w-full" variant="outline" leftIcon={<Undo2 className="size-4" />} onClick={() => onFree(slot.block_id!)}>
                Free it again
              </Button>
            )}
            {slot.status === "full" && (
              <p className="text-sm text-text-secondary">There is no room left here. To change a booking, use the list under the calendar.</p>
            )}
            {slot.status === "past" && <p className="text-sm text-text-secondary">This time has passed.</p>}
          </div>
        )}

        {mode === "book" && (
          <form onSubmit={submit} className="mt-4 space-y-3" noValidate>
            <label className="block text-sm font-medium text-text-primary">
              Name
              <input value={name} onChange={(e) => setName(e.target.value)} className={cn(inputCls, "mt-1 font-normal")} autoComplete="off" placeholder="Who is it for?" />
            </label>
            <label className="block text-sm font-medium text-text-primary">
              Phone <span className="font-normal text-text-secondary">(optional)</span>
              <input value={phone} onChange={(e) => setPhone(e.target.value)} className={cn(inputCls, "mt-1 font-normal")} inputMode="tel" autoComplete="off" placeholder="So the desk can call back" />
            </label>
            {nights && (
              <label className="block text-sm font-medium text-text-primary">
                Nights
                <input type="number" min={1} max={30} value={count} onChange={(e) => setCount(Math.max(1, Math.min(30, Number(e.target.value) || 1)))} className={cn(inputCls, "mt-1 w-24 font-normal")} />
              </label>
            )}
            {error && <p role="alert" className="text-sm text-accent-error">{error}</p>}
            <div className="flex gap-2">
              <Button type="submit" loading={saving} className="flex-1">Book</Button>
              <Button type="button" variant="ghost" onClick={() => setMode("menu")} disabled={saving}>Back</Button>
            </div>
          </form>
        )}
      </div>
    </>
  );
}
