import { forwardRef, type HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** glass: translucent blurred surface. solid: opaque surface. */
  variant?: "glass" | "solid";
  interactive?: boolean;
  padded?: boolean;
}

export const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { variant = "glass", interactive = false, padded = true, className, ...rest },
  ref
) {
  return (
    <div
      ref={ref}
      className={cn(
        "rounded-2xl border",
        variant === "glass" ? "glass" : "bg-bg-surface border-border",
        padded && "p-5",
        interactive &&
          "transition-[border-color] duration-200 ease-[var(--ease-out-expo)] hover:border-border-3",
        className
      )}
      {...rest}
    />
  );
});
