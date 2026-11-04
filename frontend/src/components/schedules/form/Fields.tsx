"use client";

import type { ReactNode } from "react";
import { Minus, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { chipClass } from "./chipStyle";

/** A numbered-free section card of the schedule form; `changed` lights it up after "Or just describe it" filled it in. */
export function Step({ id, title, hint, changed, children }: { id: string; title: string; hint?: string; changed?: boolean; children: ReactNode }) {
  return (
    <section
      aria-labelledby={id}
      className={cn("rounded-2xl border bg-bg-surface p-5 transition-[border-color,box-shadow] duration-500 md:p-6", changed ? "border-accent-primary ring-2 ring-violet-300" : "border-border")}
    >
      <h2 id={id} className="font-display text-[1.375rem] leading-tight text-text-primary">{title}</h2>
      {hint && <p className="mt-1 max-w-prose text-sm text-text-secondary">{hint}</p>}
      <div className="mt-5 space-y-5">{children}</div>
    </section>
  );
}

export function Field({ label, hint, htmlFor, children, error }: { label: string; hint?: string; htmlFor?: string; children: ReactNode; error?: string }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-sm font-semibold text-text-primary">{label}</label>
      {hint && <p className="text-sm text-text-secondary">{hint}</p>}
      {children}
      {error && <p role="alert" className="text-sm text-accent-error">{error}</p>}
    </div>
  );
}

export const inputCls =
  "h-10 w-full rounded-lg border border-border bg-bg-surface px-3 text-sm text-text-primary placeholder:text-text-muted focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary";

/** A small − / + counter that also takes typing. */
export function Stepper({ label, value, onChange, min = 1, max = 500, suffix }: { label: string; value: number; onChange: (n: number) => void; min?: number; max?: number; suffix?: string }) {
  const clamp = (n: number) => Math.max(min, Math.min(max, Math.round(n) || min));
  return (
    <div className="inline-flex items-center gap-2" role="group" aria-label={label}>
      <button type="button" aria-label={`Fewer: ${label}`} disabled={value <= min} onClick={() => onChange(clamp(value - 1))} className="grid size-10 place-items-center rounded-lg border border-border bg-bg-surface text-text-primary hover:bg-bg-base disabled:opacity-40">
        <Minus aria-hidden className="size-4" />
      </button>
      <input
        aria-label={label}
        inputMode="numeric"
        value={value}
        onChange={(e) => {
          const n = Number(e.target.value.replace(/\D/g, ""));
          if (e.target.value === "" || Number.isNaN(n)) return;
          onChange(clamp(n));
        }}
        className="h-10 w-16 rounded-lg border border-border bg-bg-surface text-center text-sm font-semibold tabular-nums text-text-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary"
      />
      <button type="button" aria-label={`More: ${label}`} disabled={value >= max} onClick={() => onChange(clamp(value + 1))} className="grid size-10 place-items-center rounded-lg border border-border bg-bg-surface text-text-primary hover:bg-bg-base disabled:opacity-40">
        <Plus aria-hidden className="size-4" />
      </button>
      {suffix && <span className="text-sm text-text-secondary">{suffix}</span>}
    </div>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="flex min-h-10 w-full items-center gap-3 text-left text-sm font-semibold text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary"
    >
      <span aria-hidden className={cn("relative h-6 w-11 shrink-0 rounded-full transition-colors", checked ? "bg-brand" : "bg-border-3")}>
        <span className={cn("absolute top-0.5 size-5 rounded-full bg-bg-surface shadow transition-[left]", checked ? "left-[22px]" : "left-0.5")} />
      </span>
      {label}
    </button>
  );
}

/** Choice chips: one is chosen. */
export function Chips<T extends string | number>({ options, value, onChange, label }: { options: { value: T; label: string }[]; value: T | null; onChange: (v: T) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-2">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          onClick={() => onChange(o.value)}
          className={chipClass(o.value === value, "min-h-10 min-w-12 rounded-full px-4 text-sm", o.value !== value && "font-medium")}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
