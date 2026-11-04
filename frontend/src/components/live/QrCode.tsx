"use client";

import { useMemo } from "react";
import { qrModules } from "@/lib/qr";

/** A QR code as SVG, drawn here (no image, no request). Dark modules use the text colour, so it follows the theme. */
export function QrCode({ value, size = 132, label }: { value: string; size?: number; label?: string }) {
  const rows = useMemo(() => {
    try {
      return qrModules(value);
    } catch {
      return null;
    }
  }, [value]);
  if (!rows) return null;
  const n = rows.length;
  const quiet = 3;
  const d = rows
    .flatMap((row, y) => {
      const runs: string[] = [];
      let x = 0;
      while (x < n) {
        if (!row[x]) {
          x++;
          continue;
        }
        let w = 1;
        while (x + w < n && row[x + w]) w++;
        runs.push(`M${x + quiet} ${y + quiet}h${w}v1h-${w}z`);
        x += w;
      }
      return runs;
    })
    .join("");
  return (
    <svg role="img" aria-label={label ?? "QR code"} viewBox={`0 0 ${n + quiet * 2} ${n + quiet * 2}`} width={size} height={size} shapeRendering="crispEdges" className="rounded-lg bg-white">
      <path d={d} fill="#111" />
    </svg>
  );
}
