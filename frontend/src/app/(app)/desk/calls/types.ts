/** GET /api/calls (backend/routers/conversations.py). Everything here is read from what the agents already stored. */
export type CallChannel = "phone" | "website" | "whatsapp" | "playground";
export type CallOutcomeKey =
  | "answered"
  | "booked"
  | "needs_approval"
  | "declined"
  | "handed_off"
  | "couldnt_answer"
  | "ended_early";

export interface CallOutcome {
  key: CallOutcomeKey;
  label: string;
  /** One plain sentence about why, when the backend can say. */
  detail: string | null;
  /** The booking request this conversation made, when the backend reports one. */
  request_id: string | null;
}

export interface CallSummary {
  id: string;
  agent_id: string | null;
  agent_name: string;
  channel: CallChannel;
  /** The receptionist rang this caller back after a missed call (conversation source "missed_call"). */
  called_back?: boolean;
  /** A practice conversation the owner started themselves (a Test call). */
  practice: boolean;
  when: string | null;
  /** Already masked by the server ("••••3210"); a number is kept only for some channels. */
  caller: string | null;
  /** The name the caller gave during the call, when it was kept. */
  caller_name?: string | null;
  /** Phone calls only, estimated from the stored turns. */
  duration_s: number | null;
  outcome: CallOutcome;
  replies: number;
  preview: string;
  /** The booking this call made (id upper case, date YYYY-MM-DD and time HH:MM in the front desk's time zone). */
  booking?: CallBooking | null;
  /** The language most replies were in ("hi", "en"). */
  language?: string | null;
  /** Nothing was said by anyone (a call that connected and dropped). */
  empty?: boolean;
}

export interface CallBooking {
  id: string;
  date: string | null;
  time: string | null;
  with: string | null;
}

export interface TranscriptLine {
  role: "caller" | "agent";
  text: string;
  at: string | null;
  actions: { name: string; ok: boolean; summary: string }[];
}

export interface CallsResponse {
  calls: CallSummary[];
  days: number;
  /** The first and last day the list covers (YYYY-MM-DD) in `timezone`. */
  from?: string;
  to?: string;
  /** VAKYA_TIMEZONE of the server, for day headings and clock times. */
  timezone?: string;
  /** Calls left out because nothing was said (when asked to hide them). */
  empty_hidden?: number;
  /** More calls matched than were returned. */
  truncated?: boolean;
}
export interface CallDetail extends CallSummary {
  transcript: TranscriptLine[];
}

/** A phone call happening right now (GET /api/phone/live). */
export interface LiveCall {
  conversation_id: string;
  call_sid: string | null;
  agent_id: string;
  agent_name: string;
  direction: "inbound" | "outbound";
  started_at: number;
  seconds: number;
  human_active: boolean;
  listeners: number;
  /** Measured on this call, per answered turn: the caller's last word to the first audio of the reply (ms). */
  response_ms?: number[];
}
