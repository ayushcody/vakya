"use client";

import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { API_URL } from "@/lib/apiBase";
import { formatDateTime } from "@/lib/formatDate";
import { BookingTimeline } from "./BookingTimeline";
import type { BookingEvidence, EvidenceItem, PublicReceipt } from "./types";

/**
 * The public page for a receipt that covers a booking (/verify?receipt=...): how the booking was made, as the four steps.
 * No sign-in. Without the owner's say-so the words are redacted and there is no audio; with it, each step can be played.
 * It never names the caller.
 */
export function BookingProof({ receiptId }: { receiptId: string }) {
  const [evidence, setEvidence] = useState<BookingEvidence | null>(null);
  const [receipt, setReceipt] = useState<PublicReceipt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [e, r] = await Promise.all([fetch(`${API_URL}/api/receipts/${encodeURIComponent(receiptId)}/booking`), fetch(`${API_URL}/api/receipts/${encodeURIComponent(receiptId)}`)]);
        if (!alive) return;
        if (e.status === 404) throw new Error("No booking proof was found for this receipt. Check the id, or it may be a receipt for a voice clip: upload the clip below instead.");
        if (!e.ok) throw new Error("Could not load the proof. Try again in a minute.");
        setEvidence((await e.json()) as BookingEvidence);
        setReceipt(r.ok ? ((await r.json()) as PublicReceipt) : null);
        setError(null);
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : "Could not reach the Vakya server.");
      } finally {
        if (alive) setLoadedFor(receiptId);
      }
    })();
    return () => {
      alive = false;
    };
  }, [receiptId]);

  const audioUrl = (item: EvidenceItem) =>
    item.audio_available ? `${API_URL}/api/receipts/${encodeURIComponent(receiptId)}/booking/audio/${item.id}` : null;

  if (loadedFor !== receiptId && !error) {
    return (
      <div className="mt-8 flex items-center gap-3 text-[var(--s-ink-2)]" role="status">
        <LoaderCircle className="h-5 w-5 animate-spin" aria-hidden /> Loading the proof...
      </div>
    );
  }
  if (error || !evidence) {
    return (
      <p role="alert" className="mt-8 rounded-lg border border-[#fda29b] bg-[#fef3f2] px-4 py-3 s-body text-[#b42318]">
        {error ?? "No booking proof was found."}
      </p>
    );
  }
  return (
    <article className="mt-8 rounded-[var(--s-r-card,1rem)] border border-[var(--s-line)] bg-[var(--s-paper)] p-5 sm:p-7" data-testid="booking-proof">
      <p className="text-[12px] font-semibold uppercase tracking-[1px] text-[var(--s-mute)]">Proof of a booking</p>
      <h2 className="h2 mt-1">How booking {evidence.booking_id} was made</h2>
      <p className="mt-2 max-w-[56ch] text-[15px] text-[var(--s-ink-2)]">
        The front desk is an AI. This is what the caller said, what it read back and the caller&apos;s yes, sealed with a signed receipt.
        {evidence.public ? " The owner made the words and recordings public." : " The owner has not made the words and recordings public, so the words are hidden and there is no audio; the hashes are real."}
      </p>
      <div className="mt-6">
        <BookingTimeline evidence={evidence} audioUrl={audioUrl} />
      </div>
      {receipt && (
        <p className="mt-5 text-[13px] text-[var(--s-mute)]">
          Signature {receipt.signature_valid ? "valid" : "INVALID"} · made {formatDateTime(receipt.created_at) || receipt.created_at}
        </p>
      )}
    </article>
  );
}
