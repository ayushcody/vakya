/** The shape of a Caller Theatre run: backend/bench/theatre/results/*.json, served read-only by GET /api/theatre/*. */

export interface TheatreCheck {
  id: string;
  label: string;
  ok: boolean;
  /** A `must` check decides pass or fail; the others are advisory and shown as notes. */
  must: boolean;
  detail: string;
}

export interface TheatreToolCall {
  name: string;
  arguments?: Record<string, unknown>;
  ok: boolean;
  result?: string;
}

export interface TheatreTurn {
  i: number;
  caller: {
    move: string;
    text: string;
    repeat?: boolean;
    /** Audio runs only: the clip as the phone line delivered it, the clip before the line, the noise measured on the mix. */
    audio?: string | null;
    audio_clean?: string | null;
    voice?: string;
    snr_db?: number | null;
    codec?: string;
  };
  /** What the agent heard (the recognised text in an audio run, the typed text in a text run). */
  heard?: string | null;
  agent: {
    text: string;
    audio?: string | null;
    tool_calls: TheatreToolCall[];
    language?: string | null;
    fallback?: boolean | null;
    canned?: boolean;
  };
  latency?: { first_answer_ms?: number | null; first_audio_ms?: number | null; total_ms?: number | null };
  wall_ms?: number;
}

export interface TheatreMetrics {
  fields_correct: number;
  fields_total: number;
  fields_correct_pct: number | null;
  turns_to_complete: number | null;
  caller_turns: number;
  read_back_before_commit: boolean | null;
  readback_catch: boolean | null;
  first_reply_ms: number | null;
  total_ms: number;
}

export interface TheatreScenario {
  id: string;
  n: number;
  title: string;
  persona: string;
  template: string;
  org: string;
  language: string;
  language_label: string;
  tags: string[];
  channel: { codec: string; snr_db: number | null };
  setup: { booking?: string; date?: string; time?: string | null; name?: string; what: string }[];
  turns: TheatreTurn[];
  error: string | null;
  ended?: string;
  verdict: { pass: boolean; reason: string; checks: TheatreCheck[]; metrics: TheatreMetrics };
}

export interface TheatreSummary {
  scenarios: number;
  passed: number;
  failed: number;
  fields_correct: number;
  fields_total: number;
  fields_correct_pct: number | null;
  median_first_reply_ms: number | null;
  median_turns_to_complete: number | null;
  median_total_ms: number | null;
}

export interface TheatreRun {
  run_id: string;
  created_at: string;
  mode: "scripted" | "live";
  input: "audio" | "text";
  input_label: string;
  mode_label: string;
  label: string;
  stack: Record<string, string>;
  scenarios: TheatreScenario[];
  summary: TheatreSummary;
  limits: string[];
}

export interface TheatreRunRow {
  run_id: string;
  mode: "scripted" | "live";
  input: "audio" | "text";
  created_at: string;
  summary: TheatreSummary;
}
