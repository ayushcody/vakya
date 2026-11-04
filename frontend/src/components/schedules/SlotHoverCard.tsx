"use client";

import { useCallback, useRef, useState, useSyncExternalStore, type FocusEvent, type PointerEvent } from "react";
import { createPortal } from "react-dom";
import { shortName, type Slot } from "@/lib/schedules";
import { SOURCE_LABEL, SourceIcon } from "./bookingWho";

const noop = () => () => {};

export interface HoverTarget {
  slot: Slot;
  rect: DOMRect;
}

/**
 * Hover and focus props for a calendar cell: a small card with who is booked in the slot appears next to it, and goes away
 * when the pointer or the focus leaves. A slot without bookings gets no card.
 */
export function useSlotHover() {
  const [target, setTarget] = useState<HoverTarget | null>(null);
  const timer = useRef<number | null>(null);
  const show = useCallback((slot: Slot, el: HTMLElement) => {
    if (timer.current) window.clearTimeout(timer.current);
    if (!slot.bookings.length) return setTarget(null);
    setTarget({ slot, rect: el.getBoundingClientRect() });
  }, []);
  const hide = useCallback(() => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setTarget(null), 80);
  }, []);
  const bind = useCallback(
    (slot: Slot) => ({
      onPointerEnter: (e: PointerEvent<HTMLElement>) => e.pointerType !== "touch" && show(slot, e.currentTarget),
      onPointerLeave: hide,
      onFocus: (e: FocusEvent<HTMLElement>) => show(slot, e.currentTarget),
      onBlur: hide,
    }),
    [show, hide]
  );
  return { target, bind };
}

export function SlotHoverCard({ target }: { target: HoverTarget | null }) {
  const hydrated = useSyncExternalStore(noop, () => true, () => false);
  if (!hydrated || !target) return null;
  const { slot, rect } = target;
  const width = 256;
  const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
  const below = rect.bottom + 12 + 40 + slot.bookings.length * 52 < window.innerHeight;
  return createPortal(
    <div
      role="tooltip"
      style={{ left, width, ...(below ? { top: rect.bottom + 6 } : { bottom: window.innerHeight - rect.top + 6 }) }}
      className="pointer-events-none fixed z-[60] rounded-xl border border-border bg-bg-surface p-3 shadow-[0_18px_40px_-16px_rgba(0,0,0,0.35)]"
    >
      <ul className="space-y-2.5">
        {slot.bookings.map((b) => (
          <li key={b.id} className="flex items-start gap-2.5">
            <span aria-hidden className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-bg-elevated text-text-secondary">
              <SourceIcon source={b.source ?? "phone"} className="size-3.5" />
            </span>
            <div className="min-w-0">
              <p className="truncate text-[15px] font-semibold leading-tight text-text-primary">{b.name ?? "A caller"}</p>
              <p className="mt-0.5 truncate text-xs text-text-secondary">
                {b.phone_masked ? `${b.phone_masked} · ` : ""}
                {SOURCE_LABEL[b.source ?? "phone"]}
                {b.nights ? ` · ${b.nights} night${b.nights === 1 ? "" : "s"}` : ""}
              </p>
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-2.5 border-t border-border pt-2 text-xs text-text-secondary">
        {slot.bookings.length === 1 ? `Click to open ${shortName(slot.bookings[0].name)}'s booking` : "Click to choose a booking"}
      </p>
    </div>,
    document.body
  );
}
