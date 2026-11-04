import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export type BadgeTone = "neutral" | "primary" | "secondary" | "success" | "warning" | "error";

const tones: Record<BadgeTone, string> = {
  // Pastel tints from the Vakya scales (light); the dark theme falls back to translucent accent washes.
  neutral: "bg-bg-base text-text-secondary border-border [[data-theme=dark]_&]:bg-bg-elevated",
  primary:
    "bg-violet-200 text-violet-700 border-transparent [[data-theme=dark]_&]:bg-accent-primary/15 [[data-theme=dark]_&]:text-accent-secondary",
  secondary:
    "bg-blue-500 text-[#0b5d73] border-transparent [[data-theme=dark]_&]:bg-sky-400/15 [[data-theme=dark]_&]:text-sky-300",
  success:
    "bg-success-tint text-accent-success border-transparent [[data-theme=dark]_&]:bg-accent-success/15",
  warning:
    "bg-orange-200 text-[#a1470d] border-transparent [[data-theme=dark]_&]:bg-accent-warning/15 [[data-theme=dark]_&]:text-warning-text",
  error:
    "bg-error-tint text-accent-error border-transparent [[data-theme=dark]_&]:bg-accent-error/15",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  dot?: boolean;
}

export function Badge({ tone = "neutral", dot = false, className, children, ...rest }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-semibold uppercase tracking-[0.5px]",
        tones[tone],
        className
      )}
      {...rest}
    >
      {dot && <span aria-hidden className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}
