import { cn } from "@/lib/utils";

/** "0.9 s": one decimal, the way a caller would feel it. */
export function seconds(ms: number): string {
  return `${(Math.max(0, ms) / 1000).toFixed(1)} s`;
}

export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const v = [...values].sort((a, b) => a - b);
  const mid = Math.floor(v.length / 2);
  return v.length % 2 ? v[mid] : Math.round((v[mid - 1] + v[mid]) / 2);
}

export const RESPONSE_NOTE = "Measured on this call: from the end of the caller's speech to the first audio of the reply.";

/**
 * How fast the front desk answered on this call: the latest turn large, a bar per turn so far (the newest lit), and the
 * median and slowest turn. Every number is measured on the call itself (phone router, end of speech to first reply audio);
 * nothing is an estimate. Renders nothing before the first answered turn.
 */
export function ResponseTime({ ms, className, compact = false }: { ms: number[]; className?: string; compact?: boolean }) {
  if (ms.length === 0) return null;
  const last = ms[ms.length - 1];
  const bars = ms.slice(-12);
  const top = Math.max(...bars, 1);
  const mid = median(ms);
  const slowest = Math.max(...ms);
  return (
    <div className={cn("flex items-center gap-3", className)} title={RESPONSE_NOTE} data-testid="response-time">
      <div className="min-w-0">
        <p className="text-xs text-text-secondary">Response</p>
        <p className="font-display text-[1.375rem] leading-none tabular-nums text-text-primary">{seconds(last)}</p>
      </div>
      <svg
        viewBox={`0 0 ${bars.length * 6} 24`}
        width={bars.length * 6}
        height={24}
        role="img"
        aria-label={`Response time per turn on this call: ${bars.map(seconds).join(", ")}`}
        className="shrink-0"
      >
        {bars.map((v, i) => {
          const h = Math.max(3, Math.round((v / top) * 24));
          return (
            <rect
              key={i}
              x={i * 6}
              y={24 - h}
              width={4}
              height={h}
              rx={1}
              fill={i === bars.length - 1 ? "var(--color-accent-primary)" : "var(--color-border-3, #c9c9c9)"}
            />
          );
        })}
      </svg>
      {!compact && mid != null && (
        <p className="text-xs leading-snug text-text-secondary tabular-nums">
          median {seconds(mid)}
          <br />
          slowest {seconds(slowest)}
        </p>
      )}
    </div>
  );
}
