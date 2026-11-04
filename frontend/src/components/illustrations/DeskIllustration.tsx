"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import { deskKey, deskType } from "./deskTypes";

interface Props {
  /** A front-desk id ("clinic", "hotel", ...). Anything unknown draws the custom front desk. */
  id?: string | null;
  /** "scene" is the 320 x 220 counter scene, "icon" the 32 x 32 glyph for selects. */
  variant?: "scene" | "icon";
  /** Width in px (scene) or side in px (icon). A scene without a size fills its container's width. */
  size?: number;
  /** Alt text. The picture is decorative (empty alt, hidden from screen readers) unless this is given. */
  label?: string;
  className?: string;
}

/** The hand-drawn illustration for a kind of front desk, served from /illustrations. Decorative by default. */
export function DeskIllustration({ id, variant = "scene", size, label, className }: Props) {
  const wanted = deskKey(id);
  const [failed, setFailed] = useState<string | null>(null);
  const key = failed === wanted ? "custom" : wanted;
  const src = `/illustrations/${key}${variant === "icon" ? "-icon" : ""}.svg`;
  const icon = variant === "icon";
  const width = size ?? (icon ? 32 : undefined);
  const height = icon ? width : size ? Math.round((size * 220) / 320) : undefined;
  return (
    // eslint-disable-next-line @next/next/no-img-element -- small static SVGs; the optimiser would only rasterise them
    <img
      src={src}
      alt={label ?? ""}
      aria-hidden={label ? undefined : true}
      data-desk={key}
      data-variant={variant}
      width={width ?? 320}
      height={height ?? 220}
      draggable={false}
      onError={() => setFailed(wanted)}
      className={cn("block select-none", icon ? "shrink-0 rounded-[28%]" : "h-auto", !icon && !size && "w-full", className)}
      style={width ? { width, height: height } : undefined}
    />
  );
}

/** "Clinic" for "clinic", and so on, for components that only have an id. */
export const deskLabel = (id: unknown): string => deskType(id).label;
