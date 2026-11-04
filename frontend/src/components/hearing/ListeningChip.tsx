import { Ear, SpellCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import type { HearingEvent } from "./types";

/** "Listening for: a name (Sunita, Rahul)" or "Probably heard 'Dr. Call Karni', corrected to 'Dr. Kulkarni' (rule learnt from call #12)". */
export function ListeningChip({ event, className }: { event: HearingEvent; className?: string }) {
  const fix = event.kind === "corrected";
  const Icon = fix ? SpellCheck : Ear;
  return (
    <p
      data-testid={fix ? "hearing-corrected" : "hearing-listening"}
      className={cn(
        "inline-flex max-w-full items-center gap-2 rounded-full border px-3 py-1 text-[13px] leading-snug",
        fix
          ? "border-[color-mix(in_srgb,var(--color-violet-700)_40%,transparent)] bg-violet-200 text-violet-700 [[data-theme=dark]_&]:bg-accent-primary/15 [[data-theme=dark]_&]:text-accent-secondary"
          : "border-border bg-bg-base text-text-secondary",
        className
      )}
    >
      <Icon aria-hidden className="size-3.5 shrink-0" />
      <span className="min-w-0">{event.label}</span>
    </p>
  );
}
