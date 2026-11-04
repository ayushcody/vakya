import { DEFAULT_CONTACT, DEFAULT_RETENTION_DAYS, NOTICE_VERSION, noticePoints } from "./noticeText";

interface Props {
  /** From the consent challenge; the defaults apply on an older backend. */
  retentionDays?: number;
  contact?: string;
  /** Wording variant: "tick" (default flow) or "strict" (read a sentence aloud). */
  mode?: "tick" | "strict";
}

/** Plain-language notice, shown before consent is recorded. The version is stored in the voice's consent record. */
export function ConsentNotice({ retentionDays, contact, mode = "strict" }: Props) {
  const points = noticePoints({
    retentionDays: retentionDays ?? DEFAULT_RETENTION_DAYS,
    contact: contact || DEFAULT_CONTACT,
    mode,
  });
  return (
    <section
      aria-label="Notice: how your voice data is used"
      className="rounded-lg border border-[var(--color-border)] p-3"
      data-testid="consent-notice"
    >
      <h4 className="text-sm font-medium text-[var(--color-text-primary)]">{mode === "tick" ? "Before you create the voice: how your voice data is used" : "Before you record: how your voice data is used"}</h4>
      <dl className="mt-2 space-y-1.5 text-sm text-[var(--color-text-secondary)]">
        {points.map((p) => (
          <div key={p.title}>
            <dt className="inline font-medium text-[var(--color-text-primary)]">{p.title}: </dt>
            <dd className="inline">{p.text}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 text-xs text-[var(--color-text-secondary)]">Notice version {NOTICE_VERSION}</p>
    </section>
  );
}
