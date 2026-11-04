import { cn } from "@/lib/utils";

/**
 * One look for every chip of the schedule form (day presets, day chips, hours presets, slot lengths): a chosen chip is a violet
 * tint with dark text and an accent border (tokens `--chip-on-*` in globals.css, 9:1 contrast), an unchosen one is plain.
 * Every chip has the same focus ring.
 */
export const CHIP_FOCUS = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary";

export const CHIP_ON = "border-[var(--chip-on-border)] bg-[var(--chip-on-bg)] text-[var(--chip-on-text)] font-semibold shadow-[inset_0_0_0_1px_var(--chip-on-border)]";
export const CHIP_OFF = "border-border bg-bg-surface text-text-primary hover:bg-bg-base";

export function chipClass(on: boolean, ...extra: (string | false | undefined)[]) {
  return cn("border transition-colors", CHIP_FOCUS, on ? CHIP_ON : CHIP_OFF, ...extra);
}
