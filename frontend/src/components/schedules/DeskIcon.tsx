"use client";

import { useState } from "react";
import Image from "next/image";
import { ConciergeBell, LayoutGrid } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * The glyph of a front-desk type (/illustrations/{id}-icon.svg). Until the illustration exists, or for a desk made from
 * scratch, a plain bell stands in; "all" shows the grid mark for "All front desks".
 */
export function DeskIcon({ templateId, all, size = 22, className }: { templateId?: string | null; all?: boolean; size?: number; className?: string }) {
  const [broken, setBroken] = useState<string | null>(null);
  if (all) return <LayoutGrid aria-hidden className={cn("shrink-0 text-text-secondary", className)} style={{ width: size * 0.8, height: size * 0.8 }} />;
  if (!templateId || broken === templateId) {
    return <ConciergeBell aria-hidden className={cn("shrink-0 text-text-secondary", className)} style={{ width: size * 0.8, height: size * 0.8 }} />;
  }
  return (
    <Image
      src={`/illustrations/${encodeURIComponent(templateId)}-icon.svg`}
      alt=""
      aria-hidden
      width={size}
      height={size}
      unoptimized
      onError={() => setBroken(templateId)}
      className={cn("shrink-0", className)}
    />
  );
}
