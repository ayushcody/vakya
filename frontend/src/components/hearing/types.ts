/** Ears that learn (backend/routers/hearing.py, services/hearing.py): what the owner has taught a front desk about how its callers sound. */

/** Where a correction came from: a call and the line in it, or the owner typing it in Settings. */
export type LearntFrom = { conversation_id: string; turn?: number } | "owner";

export interface HearingRule {
  id: string;
  heard: string;
  meant: string;
  learnt_from: LearntFrom;
  created_at: string | null;
  last_used_at: string | null;
  uses: number;
}

export interface VocabularyTerm {
  term: string;
  added_by: "correction" | "owner";
  created_at: string | null;
}

/** GET /api/hearing/{agent_id}. */
export interface HearingSummary {
  agent_id: string;
  rules: HearingRule[];
  vocabulary: VocabularyTerm[];
  /** Gnani's Prisma takes at most this many substitution rules per request. */
  rule_limit: number;
  rules_used: number;
  vocabulary_limit: number;
  note: string;
}

/** POST /api/hearing/{agent_id}/corrections. */
export interface TeachResponse {
  rule: HearingRule;
  replaced: HearingRule | null;
  vocabulary_added: boolean;
  vocabulary_full: boolean;
  rules_used: number;
  /** "From now on Vakya listens for 'Dr. Kulkarni' on this desk." */
  message: string;
}

/** The live event `hearing` (backend/services/call_events.py): what the desk is listening for, or a correction that probably ran. */
export interface HearingEvent {
  seq: number;
  type: "hearing";
  agent_id: string;
  conversation_id: string | null;
  turn_id: string | null;
  kind: "listening" | "corrected";
  label: string;
  expecting?: "name" | "when" | "digits" | "yes_no" | "open";
  kinds?: string[];
  names?: string[];
  probable?: boolean;
  rule?: { id: string; heard: string; meant: string; learnt_from: LearntFrom };
}
