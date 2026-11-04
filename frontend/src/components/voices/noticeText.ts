/**
 * Plain-language notice shown in the clone wizard before consent is recorded.
 *
 * Keep NOTICE_VERSION equal to NOTICE_VERSION in backend/services/consent_record.py (a backend test checks this).
 * Bump both when the wording changes: the version is stored in each voice's consent record.
 * Not legal advice: the operator should have the wording checked for the laws that apply to them.
 * TODO: add a Hindi version (and other Indian languages) of this notice.
 */
export const NOTICE_VERSION = "2026-11-03";

export interface NoticePoint {
  title: string;
  text: string;
}

export interface NoticeFacts {
  /** How long a voice is kept (server setting VOICE_RETENTION_DAYS). */
  retentionDays: number;
  /** Who to contact about a voice; falls back to whoever runs this server. */
  contact: string;
  /** "tick" (one checkbox, no recording of the person) or "strict" (the person reads a sentence aloud). Default strict. */
  mode?: "tick" | "strict";
}

export const DEFAULT_RETENTION_DAYS = 365;
export const DEFAULT_CONTACT = "the person who runs this Vakya desk";

export function noticePoints({ retentionDays, contact, mode = "strict" }: NoticeFacts): NoticePoint[] {
  const tick = mode === "tick";
  return [
    {
      title: "What we collect",
      text: tick
        ? "Your voice sample, a numeric fingerprint (embedding) of your voice, and a record that the consent box was ticked (when, the name given and a hash of the sample)."
        : "Your voice sample, a recording of you reading the consent sentence, that sentence as text, and a numeric fingerprint (embedding) of your voice.",
    },
    {
      title: "Why",
      text: "To make an AI version of your voice for use in Vakya, and to show that you agreed. We use it for nothing else.",
    },
    {
      title: "How long we keep it",
      text: `${retentionDays} days from today, or until you withdraw, whichever comes first.`,
    },
    {
      title: "How to withdraw",
      text: `At any time, choose "Withdraw consent and delete" on the voice in the Voices page. That deletes the sample, the embedding and the consent ${tick ? "record" : "recording"}. Clips already made are kept and marked as made with a voice whose owner withdrew consent.`,
    },
    {
      title: "Contact",
      text: `Questions or requests: ${contact}.`,
    },
  ];
}
