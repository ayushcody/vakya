/** What the receptionist remembers about one caller (GET /api/callers, backend/services/caller_profiles.py public_view). The number is already masked. */
export interface CallerFact {
  key: string;
  label: string;
  value: string;
}

export interface CallerPerson {
  name: string;
  relation?: string;
  age_band?: string;
  last_booked_at?: string;
}

export interface CallerProfile {
  id: string;
  /** The opaque reference the bookings list links with: /desk/callers?caller=<caller_ref>. Same as `id`. */
  caller_ref?: string;
  number_masked: string;
  display_name: string | null;
  /** "hi", "mr", "en", "hinglish", ... */
  preferred_language: string | null;
  last_call_at: string | null;
  last_outcome: "booked" | "asked" | "unanswered" | null;
  last_booking: { date?: string; time?: string; purpose?: string } | null;
  follow_ups: { note: string; at: string }[];
  summary: string | null;
  call_count: number;
  /** The details of the agent's trade (a preferred doctor, a party size), as chips. */
  facts?: CallerFact[];
  /** The people booked for on this number (a mother booking for her son), and the last one booked for. */
  people?: CallerPerson[];
  last_person?: string | null;
  /** "phone", "web call" (the caller gave the number in a browser call: not verified) or "owner". */
  source?: "phone" | "web call" | "owner";
  /** The conversations of this caller; `returning` is true when the caller was already known when that call began. */
  calls: { conversation_id: string; at: string | null; returning: boolean }[];
  consent_note: string;
  retention_days: number;
  expires_at: string | null;
}

export interface CallerEdit {
  display_name?: string | null;
  preferred_language?: string | null;
  /** {key: value}; null removes a detail. */
  facts?: Record<string, string | null>;
  people?: { name: string; relation?: string | null; age_band?: string | null }[];
  last_person?: string | null;
  add_note?: string;
  /** The notes that stay (the owner removes the others). */
  follow_ups?: string[];
}
