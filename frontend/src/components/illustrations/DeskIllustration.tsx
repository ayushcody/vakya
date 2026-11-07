"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import { ACCENTS, deskKey, deskType } from "./deskTypes";

interface Props {
  /** A front-desk id ("clinic", "hotel", ...). Anything unknown draws the custom front desk. */
  id?: string | null;
  /** "scene" is a 320 x 220 panel with the trade's one object drawn large, "icon" the 32 x 32 glyph for selects. */
  variant?: "scene" | "icon";
  /** Width in px (scene) or side in px (icon). A scene without a size fills its container's width. */
  size?: number;
  /** Alt text. The picture is decorative (empty alt, hidden from screen readers) unless this is given. */
  label?: string;
  className?: string;
}

/** The picture for a kind of front desk, served from /illustrations. Decorative by default.
 *  Each trade is one distinct object (a stethoscope, a desk bell, scissors...) on its own tint. The scene is that object drawn
 *  large, not a shared counter layout, so no two trades look alike. */
export function DeskIllustration({ id, variant = "scene", size, label, className }: Props) {
  const wanted = deskKey(id);
  const [failed, setFailed] = useState<string | null>(null);
  const key = failed === wanted ? "custom" : wanted;
  const src = `/illustrations/${key}-icon.svg`;
  const glyph = (side: number | string, extra?: string) => (
    // eslint-disable-next-line @next/next/no-img-element -- small static SVGs; the optimiser would only rasterise them
    <img
      src={src}
      alt={label ?? ""}
      aria-hidden={label ? undefined : true}
      data-desk={key}
      data-variant={variant}
      width={typeof side === "number" ? side : 32}
      height={typeof side === "number" ? side : 32}
      draggable={false}
      onError={() => setFailed(wanted)}
      className={cn("block shrink-0 select-none rounded-[28%]", extra)}
      style={{ width: side, height: side }}
    />
  );
  if (variant === "icon") return glyph(size ?? 32, className);
  const height = size ? Math.round((size * 220) / 320) : undefined;
  return (
    <span
      className={cn("grid select-none place-items-center", !size && "aspect-[320/220] w-full", className)}
      style={{ background: ACCENTS[deskType(key).accent].tint, ...(size ? { width: size, height } : {}) }}
    >
      {glyph(size ? Math.round(size * 0.34) : "34%")}
    </span>
  );
}

/** "Clinic" for "clinic", and so on, for components that only have an id. */
export const deskLabel = (id: unknown): string => deskType(id).label;
