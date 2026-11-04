/** Response of the public POST /api/verify. */
export type Verdict = "made_with_vakya" | "not_detected";

export interface PublicReceipt {
  receipt_id: string;
  kind: "generation" | "agent_reply" | "booking";
  created_at: string;
  engine: string | null;
  voice_id: string | null;
  voice_name: string | null;
  consent_id: string | null;
  consent_verified: boolean | null;
  signature_valid: boolean;
  /** When the voice owner withdrew consent (or the voice was deleted after its retention period); null/absent while
   *  the voice still exists. Read from a withdrawal log: the signed receipt itself never changes. */
  voice_withdrawn_at?: string | null;
  /** "preset_voice" | "active" | "unverified" | "withdrawn" | "retention_ended" (see docs/RECEIPT_SPEC.md). */
  voice_consent_status?: string;
  /** "withdrawn" (the owner withdrew consent) or "retention_expired" (deleted when its retention period ended). */
  voice_removed_reason?: "withdrawn" | "retention_expired" | null;
}

export interface VerifyResponse {
  /** The C2PA content manifest in the file: null when this server cannot read one, {present: false} when there is none. */
  manifest?: { present: boolean; generator?: string | null; [key: string]: unknown } | null;
  match: {
    exact: boolean;
    receipt: PublicReceipt | null;
    signature_valid: boolean;
  };
  verdict: Verdict;
}

/** One step of the proof of a booking (GET /api/receipts/{id}/booking, GET /api/bookings/{id}/evidence). */
export interface EvidenceItem {
  id: string;
  role: "field" | "readback" | "confirm";
  field: "name" | "phone" | "date" | "time" | "schedule" | null;
  label: string | null;
  speaker: "caller" | "agent";
  turn_id: string | null;
  at: string | null;
  /** Redacted unless the owner is looking or made the proof public. */
  transcript: string;
  language: string | null;
  basis: "heard" | "text" | "inferred" | null;
  typed: boolean;
  transcript_sha256: string;
  audio_sha256: string | null;
  item_sha256: string | null;
  has_audio: boolean;
  audio_available: boolean;
  duration_s: number | null;
  sample_rate: number | null;
  reply_receipt_id: string | null;
}

export interface BookingEvidence {
  evidence_id: string;
  receipt_id: string;
  booking_id: string;
  public: boolean;
  created_at: string | null;
  complete: boolean;
  /** The recordings and transcripts were deleted at the end of the retention period; only the hashes remain. */
  deleted: boolean;
  retention_days: number;
  manifest_sha256: string;
  /** Every hash still matches and the signed receipt that covers them verifies. */
  verified: boolean;
  verification: { receipt_ok: boolean; manifest_ok: boolean; reason: string | null };
  schedule_name?: string | null;
  start?: string | null;
  date?: string;
  time?: string;
  conversation_id?: string | null;
  items: EvidenceItem[];
}
