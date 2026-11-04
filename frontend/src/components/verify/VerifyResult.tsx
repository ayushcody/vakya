import { CircleHelp, ShieldCheck } from "lucide-react";
import { formatDate, formatDateTime } from "@/lib/formatDate";
import type { PublicReceipt, Verdict, VerifyResponse } from "./types";

const VERDICTS: Record<Verdict, { title: string; body: string; tone: string; Icon: typeof ShieldCheck }> = {
  made_with_vakya: {
    title: "Made with Vakya",
    body: "This file is an exact copy of a clip that matches a receipt signed by this server.",
    tone: "bg-[var(--s-marigold)] text-[var(--s-marigold-ink)]",
    Icon: ShieldCheck,
  },
  not_detected: {
    title: "Not detected",
    body: "No matching receipt was found on this server. That does not show the clip is genuine: it may come from another tool, or have been edited or re-encoded since it was made.",
    tone: "bg-[var(--s-line)] text-[var(--s-ink)]",
    Icon: CircleHelp,
  },
};

const KIND_LABEL: Record<PublicReceipt["kind"], string> = {
  generation: "Generated clip",
  agent_reply: "Agent reply",
  booking: "Booking proof",
};

const ENGINE_LABEL: Record<string, string> = {
  timbre: "Gnani Timbre",
  timbre_clone: "Gnani Timbre (voice clone)",
  dialogue: "Dialogue",
};

export function formatCreated(iso: string): string {
  return formatDateTime(iso) || iso;
}

function consentLabel(r: PublicReceipt): { text: string; good: boolean } {
  if (r.voice_withdrawn_at && r.consent_id) {
    return { text: r.consent_verified === true ? "Consent verified when made, now withdrawn" : "Consent now withdrawn", good: false };
  }
  if (r.consent_verified === true) return { text: "Consent verified", good: true };
  if (r.consent_id) return { text: "Consent recorded, not verified", good: false };
  if (r.voice_id) return { text: "No consent record", good: false };
  return { text: "Preset voice (no cloning)", good: false };
}

/** The sentence shown for a clip whose voice has since been removed (null while the voice still exists). */
export function withdrawalNote(r: PublicReceipt): string | null {
  if (!r.voice_withdrawn_at) return null;
  const when = formatDay(r.voice_withdrawn_at);
  return r.voice_removed_reason === "retention_expired"
    ? `This voice was deleted on ${when} when its retention period ended.`
    : `The voice owner withdrew consent on ${when}.`;
}

function formatDay(iso: string): string {
  return formatDate(iso) || iso;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 py-3 sm:flex-row sm:items-baseline sm:gap-6">
      <dt className="w-40 shrink-0 text-[13px] text-[var(--s-mute)]">{label}</dt>
      <dd className="min-w-0 text-[15px] text-[var(--s-ink)]">{children}</dd>
    </div>
  );
}

function Receipt({ receipt, exact }: { receipt: PublicReceipt; exact: boolean }) {
  const consent = consentLabel(receipt);
  const withdrawn = withdrawalNote(receipt);
  return (
    <dl className="divide-y divide-[var(--s-line)]" data-testid="receipt">
      <Row label="Created">{formatCreated(receipt.created_at)}</Row>
      <Row label="Type">{KIND_LABEL[receipt.kind] ?? receipt.kind}</Row>
      <Row label="Engine">{(receipt.engine && ENGINE_LABEL[receipt.engine]) || receipt.engine || "Unknown"}</Row>
      <Row label="Voice">{receipt.voice_name || "Unknown"}</Row>
      <Row label="Consent">
        <span
          className={`inline-flex items-center rounded-md px-2.5 py-0.5 text-[12px] font-semibold uppercase tracking-[1px] ${
            consent.good ? "bg-[var(--violet-200)] text-black" : "border border-[var(--s-bar)] text-[var(--s-ink-2)]"
          }`}
        >
          {consent.text}
        </span>
      </Row>
      {withdrawn && (
        <Row label="Voice status">
          <span
            className="inline-flex items-center rounded-md border border-[#d92d20] px-2.5 py-0.5 text-[13px] font-medium text-[#d92d20]"
            data-testid="voice-withdrawn"
          >
            Voice withdrawn
          </span>
          <p className="mt-1.5 text-[14px] text-[var(--s-ink-2)]">
            {withdrawn} The receipt is kept as a record that the clip was made while consent stood.
          </p>
        </Row>
      )}
      <Row label="Match">{exact ? "Exact copy of the original file" : "Not an exact copy"}</Row>
      <Row label="Signature">
        <span className={receipt.signature_valid ? "" : "font-medium text-[#d92d20]"}>
          {receipt.signature_valid ? "Valid: the receipt has not been altered" : "Invalid: the receipt does not verify"}
        </span>
      </Row>
      <Row label="Receipt">
        <code className="break-all font-mono text-[13px] text-[var(--s-ink-2)]">{receipt.receipt_id}</code>
      </Row>
    </dl>
  );
}

export function VerifyResult({ result }: { result: VerifyResponse }) {
  const v = VERDICTS[result.verdict];
  const manifest = result.manifest;
  return (
    <article className="overflow-hidden rounded-[var(--s-r-card,1rem)] border border-[var(--s-line)] bg-[var(--s-paper)]" data-testid="verify-result">
      <header className={`flex items-start gap-4 px-6 py-7 sm:px-8 ${v.tone}`}>
        <v.Icon className="mt-1 h-9 w-9 shrink-0" aria-hidden />
        <div>
          <h2 className="h2">{v.title}</h2>
          <p className="mt-2 max-w-[52ch] text-[15px] leading-relaxed opacity-90">{v.body}</p>
        </div>
      </header>

      <div className="px-6 py-6 sm:px-8">
        {manifest?.present ? (
          <p className="mb-4 text-[13px] text-[var(--s-mute)]" data-testid="manifest-note">
            This file also carries a C2PA content manifest{manifest.generator ? ` from ${manifest.generator}` : ""}.
          </p>
        ) : null}

        {result.match.receipt && <Receipt receipt={result.match.receipt} exact={result.match.exact} />}
      </div>
    </article>
  );
}
