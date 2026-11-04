import { cn } from "@/lib/utils";
import type { CallerFact, CallerPerson } from "./types";

const chip = "inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-bg-base px-2.5 py-1 text-xs text-text-primary";

/** The trade details Vakya keeps for a caller, as "Preferred doctor: Dr Rao" chips. */
export function FactChips({ facts, className }: { facts?: CallerFact[]; className?: string }) {
  if (!facts || facts.length === 0) return null;
  return (
    <ul className={cn("flex flex-wrap gap-1.5", className)} aria-label="Remembered details" data-testid="fact-chips">
      {facts.map((f) => (
        <li key={f.key} className={chip}>
          <span className="shrink-0 text-text-secondary">{f.label}</span>
          <span className="min-w-0 break-words font-medium">{f.value}</span>
        </li>
      ))}
    </ul>
  );
}

export function personDetail(p: CallerPerson): string {
  return [p.relation, p.age_band].filter(Boolean).join(", ");
}

/** The people booked for on one number: "Sunita", "Rohan (son, child)"; the last one booked for is marked. */
export function PeopleChips({ people, last, className }: { people?: CallerPerson[]; last?: string | null; className?: string }) {
  if (!people || people.length === 0) return null;
  return (
    <ul className={cn("flex flex-wrap gap-1.5", className)} aria-label="People on this number" data-testid="people-chips">
      {people.map((p) => {
        const detail = personDetail(p);
        const isLast = !!last && p.name.toLowerCase() === last.toLowerCase();
        return (
          <li key={p.name} className={cn(chip, isLast && "border-text-secondary")}>
            <span className="font-medium">{p.name}</span>
            {detail && <span className="text-text-secondary">({detail})</span>}
            {isLast && <span className="text-text-secondary">· last booked</span>}
          </li>
        );
      })}
    </ul>
  );
}
