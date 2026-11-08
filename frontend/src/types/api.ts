export interface Voice {
  id: string;
  name: string;
  description?: string | null;
  language?: string | null;
  engine?: string | null;
  audio_url?: string | null;
  sample_url?: string | null;
  transcript?: string | null;
  created_at: string;
  [key: string]: unknown;
}

export interface HealthStatus {
  status: string;
  models_loaded?: string[];
  supabase?: { configured: boolean; reachable?: boolean };
  disk_free_gb?: number;
  llm?: LlmStatus;
  [key: string]: unknown;
}

export type LlmProviderName = "hosted";

/** Language-model status inside GET /api/health. */
export interface LlmStatus {
  active: LlmProviderName | null;
  model: string | null;
  providers: { name: string; available: boolean; reason?: string | null }[];
  usage_today: Record<string, { requests: number; tokens: number }>;
}

/** GET /api/agents (`{ agents: Agent[] }`). */
export interface Agent {
  id: string;
  name: string;
  greeting: string;
  system_instructions: string;
  supported_languages: string[];
  voice_id: string | null;
  /** The agent says it is an AI at the start of each conversation (default true). */
  disclose_ai?: boolean;
  /** Names of the MCP tool servers the agent may use, e.g. "appointments". */
  tools?: string[];
  /** The front-desk type it was created from ("clinic", "hotel", ...). The server may not store it yet; the UI then falls back to "custom". */
  template_id?: string | null;
  created_at: string;
  document_count: number;
}

/** Fields PUT /api/agents/{id} accepts (all optional). */
export type AgentUpdate = Partial<
  Pick<Agent, "name" | "greeting" | "system_instructions" | "supported_languages" | "voice_id" | "disclose_ai" | "tools">
>;

export interface AgentDocument {
  id: string;
  filename: string;
  chunk_count: number;
  created_at: string;
}

/** GET /api/agent-templates item. */
export interface AgentTemplate {
  id: string;
  name: string;
  role: string;
  languages: string[];
  greeting: string;
  document: string;
  suggested_questions: string[];
  /** Path of a sample PDF to download (templates that come with one). */
  sample_pdf?: string | null;
  /** The front-desk type (set by the server since the generic front desks): "Clinic", "Hotel", "Salon and spa". */
  type_label?: string;
  /** One line under the type label. */
  tagline?: string;
  /** Illustration id (equals the template id). */
  illustration?: string;
  /** Accent colour key for the card and chips. */
  accent?: DeskAccent;
  /** How the front desk sounds: a tone, three trait words and one sample line in English and Hindi. */
  persona?: TemplatePersona;
  /** Default schedules the server creates with the agent (no ids yet). */
  schedules?: TemplateScheduleSpec[];
}

export type DeskAccent = "violet" | "orange" | "blue" | "green" | "rose" | "amber" | "teal" | "slate";

export interface TemplatePersona {
  tone: string;
  traits: string[];
  sample_line: { en: string; hi: string };
}

/** A default schedule inside a template. The full model lives in lib/schedules.ts; only what the gallery shows is typed here. */
export interface TemplateScheduleSpec {
  name: string;
  kind?: "slots" | "nights";
  slot_minutes?: number;
  capacity?: number;
}

/** One schedule created together with an agent (POST /api/agents/from-template returns them as `schedules`). */
export interface CreatedScheduleRef {
  id: string;
  name: string;
  kind?: "slots" | "nights";
  slot_minutes?: number;
  capacity?: number;
  summary?: { hours_text?: string } | null;
}

/** One number the owner may call: GET /api/phone/status (`number` is E.164, `masked` is for display). */
export interface PhoneAllowedNumber {
  number: string;
  masked: string;
  label: string;
  /** "env": from PHONE_ALLOWED_NUMBERS in backend/.env (cannot be removed in the app); "saved": added in the app. */
  source: "env" | "saved";
  /** Set for source "saved" only. */
  id: string | null;
}

/** POST /api/phone/numbers */
export interface SavedPhoneNumber {
  id: string;
  label: string;
  number: string;
  masked: string;
  source: "saved";
}

/** The phone provider Vakya calls through. Vobiz is the only one. */
export type PhoneProviderName = "vobiz";

/** The provider in GET /api/phone/status (never a key or token). */
export interface PhoneProviderStatus {
  name: PhoneProviderName;
  /** How it reads to a person: "Vobiz". */
  display: string;
  /** VOBIZ_AUTH_ID, VOBIZ_AUTH_TOKEN and VOBIZ_CALLER_ID are all set in backend/.env. */
  configured: boolean;
  enabled: boolean;
  /** false: written from the provider's docs, not yet run against a real account. */
  verified: boolean;
  verification_note?: string;
  caller_id_masked?: string | null;
}

/** GET /api/phone/status (owner only). */
export interface PhoneStatus {
  configured: boolean;
  /** Names of the missing backend/.env settings, e.g. "VOBIZ_AUTH_ID". */
  missing: string[];
  provider?: PhoneProviderName;
  provider_display?: string;
  provider_verified?: boolean;
  allowed_numbers: PhoneAllowedNumber[];
  allowed_numbers_set: boolean;
  caller_id_masked: string | null;
  /** PUBLIC_BASE_URL is set, so the inbound link can be built. */
  inbound_ready: boolean;
  public_base_url: string | null;
  max_call_seconds: number;
  providers?: PhoneProviderStatus[];
}

export type PhoneCallState =
  | "queued"
  | "ringing"
  | "in-progress"
  | "completed"
  | "failed"
  | "busy"
  | "no-answer"
  | "canceled"
  | "unknown";

/** POST /api/phone/call */
export interface PhoneCallPlaced {
  status: "calling";
  to: string;
  call_sid: string | null;
  agent_id: string;
  provider?: PhoneProviderName;
}

/** GET /api/phone/call/{call_sid} */
export interface PhoneCallStatus {
  call_sid: string;
  status: PhoneCallState;
  duration_s: number | null;
  started_at: string | null;
  ended_at: string | null;
  /** Vobiz's explanation when the call failed, was busy, unanswered or canceled. */
  message?: string | null;
}

/** POST /api/phone/inbound-link: the Answer URL for the Vobiz application of the number that takes calls. */
export interface PhoneInboundLink {
  answer_url: string;
  method: "POST";
  valid_days: number;
  /** One line on where to paste it in the Vobiz console. */
  how?: string;
  configured?: boolean;
}


/** One speech engine of GET /api/engines. Gnani's hosted engines (`timbre`, `timbre_clone`) also say whether they can run. */
export interface EngineInfo {
  id: string;
  name: string;
  licence?: string;
  available?: boolean;
  unavailable_reason?: string | null;
  [key: string]: unknown;
}

/** GET /api/consent/mode: how consent is collected when a voice is cloned. */
export interface ConsentMode {
  consent_mode: "tick" | "strict";
  statement?: string;
  notice_version?: string;
  retention_days?: number;
  contact?: string;
}

/** GET /api/privacy/attestation: the signed statement of how this server is set up. */
export interface PrivacyAttestation {
  statement?: { claim?: string; [key: string]: unknown };
  signature_ed25519?: string;
  key_id?: string;
}
