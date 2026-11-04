"use client";

import { chipClass } from "./chipStyle";
import { DAY_KEYS, DAY_LONG, DAY_PRESETS, DAY_SHORT, activePreset, toggleDay, type DayKey } from "@/lib/schedules";

/** Seven day chips that always show, plus one-tap presets. */
export function DayChips({ open, onChange, nights = false }: { open: DayKey[]; onChange: (next: DayKey[]) => void; nights?: boolean }) {
  const active = activePreset(open);
  return (
    <div className="space-y-3">
      <div role="group" aria-label={nights ? "Nights guests can book" : "Days you are open"} className="grid grid-cols-7 gap-1.5 sm:gap-2">
        {DAY_KEYS.map((d) => {
          const on = open.includes(d);
          return (
            <button
              key={d}
              type="button"
              aria-pressed={on}
              aria-label={DAY_LONG[d]}
              onClick={() => onChange(toggleDay(open, d))}
              className={chipClass(on, "flex min-h-14 flex-col items-center justify-center rounded-xl text-sm font-semibold", !on && "text-text-secondary")}
            >
              <span>{DAY_SHORT[d]}</span>
              <span aria-hidden className="mt-0.5 text-[10px] font-medium uppercase tracking-[0.5px]">
                {on ? "Open" : "Closed"}
              </span>
            </button>
          );
        })}
      </div>
      <div className="flex flex-wrap gap-2" role="group" aria-label="Quick choices">
        {DAY_PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            aria-pressed={active?.id === p.id}
            onClick={() => onChange([...p.days])}
            className={chipClass(active?.id === p.id, "min-h-9 rounded-full px-3.5 text-[13px]", active?.id !== p.id && "font-medium")}
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  );
}
