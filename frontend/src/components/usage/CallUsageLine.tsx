"use client";

import { Badge } from "@/components/ui";
import { degradedLabel, formatCredits } from "./format";
import { useCallUsage } from "./useUsage";

/** Under a call in the calls list: the speech credits it used ("estimated") and any time Gnani was busy or down. Renders
 *  nothing when the server has no usage for the call (Gnani off, or the call was made before the ledger existed). */
export function CallUsageLine({ callId }: { callId: string }) {
  const { data } = useCallUsage();
  const usage = data?.calls?.[callId];
  if (!usage || usage.requests === 0) return null;
  const reasons = usage.degraded_reasons ?? [];
  const actions = usage.actions ?? [];
  const label = degradedLabel(reasons[0], actions.find((a) => a === "callback" || a === "callback_offered") ?? actions[0]);
  return (
    <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-text-secondary" data-testid="call-usage">
      <span title="Estimated from rates; not an invoice">
        {usage.credits > 0 ? `About ${formatCredits(usage.credits)} credits (estimated)` : "No credits used (answered from memory)"}
      </span>
      {usage.degraded > 0 && (
        <Badge tone={label.includes("callback") ? "error" : "warning"} title={reasons.join(", ")}>
          {label}{usage.degraded > 1 ? ` ×${usage.degraded}` : ""}
        </Badge>
      )}
    </span>
  );
}
