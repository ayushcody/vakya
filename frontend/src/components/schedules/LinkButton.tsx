import Link from "next/link";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/** A link that looks like a Button (same height, radius and weights). */
export function LinkButton({ variant = "solid", className, ...rest }: ComponentProps<typeof Link> & { variant?: "solid" | "outline" | "ghost" }) {
  return (
    <Link
      className={cn(
        "inline-flex h-10 min-h-10 items-center justify-center gap-2 rounded-lg px-4 text-sm font-semibold transition-[opacity,background-color] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary",
        variant === "solid" && "bg-brand text-[var(--color-on-accent)] hover:opacity-90",
        variant === "outline" && "border border-border bg-bg-surface text-text-primary hover:bg-bg-base",
        variant === "ghost" && "text-text-secondary hover:bg-bg-base hover:text-text-primary",
        className
      )}
      {...rest}
    />
  );
}
