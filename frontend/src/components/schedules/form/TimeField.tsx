"use client";

import { useId, useState } from "react";
import { cn } from "@/lib/utils";
import { fromMin, parseTimeInput, timeWords, toMin } from "@/lib/schedules";

const STEPS = Array.from({ length: 96 }, (_, i) => fromMin(i * 15));

/**
 * A time you can type ("5:30 pm", "17:30", "noon") or pick from the list in 15-minute steps. It is committed when you leave
 * the field or press Enter; text that is not a time goes back to the last good value with a hint.
 * `after` (a start time) helps read a bare "5" as 5 pm in an end field.
 */
export function TimeField({
  label,
  value,
  onChange,
  after,
  invalid,
  className,
  allowEndOfDay = false,
}: {
  label: string;
  value: string;
  onChange: (hhmm: string) => void;
  after?: string;
  invalid?: boolean;
  className?: string;
  allowEndOfDay?: boolean;
}) {
  const id = useId();
  // Remounted by the key whenever the value changes from outside, so the typed text never goes stale.
  return <Inner key={value} id={id} label={label} value={value} onChange={onChange} after={after} invalid={invalid} className={className} allowEndOfDay={allowEndOfDay} />;
}

function Inner({ id, label, value, onChange, after, invalid, className, allowEndOfDay }: { id: string; label: string; value: string; onChange: (v: string) => void; after?: string; invalid?: boolean; className?: string; allowEndOfDay: boolean }) {
  const [text, setText] = useState(Number.isNaN(toMin(value)) ? "" : timeWords(value));
  const [hint, setHint] = useState<string | null>(null);
  const commit = () => {
    const parsed = parseTimeInput(text, { after: after !== undefined && !Number.isNaN(toMin(after)) ? toMin(after) : undefined });
    if (!parsed || (parsed === "24:00" && !allowEndOfDay && after === undefined)) {
      setHint("Type a time like 9 am or 5:30 pm.");
      setText(Number.isNaN(toMin(value)) ? "" : timeWords(value));
      return;
    }
    setHint(null);
    if (parsed !== value) onChange(parsed);
    else setText(timeWords(parsed));
  };
  const list = `${id}-times`;
  return (
    <span className={cn("relative inline-flex w-[7.25rem] flex-col", className)}>
      <input
        aria-label={label}
        aria-invalid={invalid || !!hint || undefined}
        aria-describedby={hint ? `${id}-hint` : undefined}
        list={list}
        value={text}
        inputMode="text"
        autoComplete="off"
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          }
        }}
        onFocus={(e) => e.currentTarget.select()}
        className={cn(
          "h-10 w-full rounded-lg border bg-bg-surface px-3 text-sm tabular-nums text-text-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary",
          invalid || hint ? "border-accent-error" : "border-border"
        )}
      />
      <datalist id={list}>
        {STEPS.map((s) => (
          <option key={s} value={timeWords(s)} />
        ))}
      </datalist>
      {hint && (
        <span id={`${id}-hint`} role="status" className="absolute left-0 top-full z-10 mt-1 w-44 text-xs text-accent-error">
          {hint}
        </span>
      )}
    </span>
  );
}
