"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useRef, useState, type DragEvent, type FormEvent } from "react";
import { ArrowUpRight, FileAudio, LoaderCircle, Upload } from "lucide-react";
import { Logo } from "@/components/Logo";
import { siteFont, siteSerif } from "@/components/site/siteFont";
import { BookingProof } from "@/components/verify/BookingProof";
import { VerifyResult } from "@/components/verify/VerifyResult";
import type { VerifyResponse } from "@/components/verify/types";
import { API_URL, ApiError, normalizeError } from "@/lib/apiBase";

const MAX_BYTES = 20 * 1024 * 1024;
const EXTENSIONS = [".wav", ".mp3", ".m4a", ".webm", ".flac", ".ogg"];

function sizeLabel(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** Public: no sign-in, no Authorization header. The backend rate-limits this endpoint. */
async function verifyClip(file: File): Promise<VerifyResponse> {
  const form = new FormData();
  form.append("file", file);
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/verify`, { method: "POST", body: form });
  } catch {
    throw new ApiError(
      "Could not reach the Vakya server. Check that it is running and try again.",
      0,
      "network_error",
    );
  }
  if (!res.ok) {
    const err = await normalizeError(res);
    if (res.status === 429)
      throw new ApiError(
        "Too many checks in a short time. Wait a minute and try again.",
        429,
        err.code,
      );
    throw err;
  }
  return (await res.json()) as VerifyResponse;
}

function VerifyPage() {
  const params = useSearchParams();
  const receiptId = (params?.get("receipt") ?? "").trim();
  const [lookup, setLookup] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<VerifyResponse | null>(null);

  const check = useCallback(async (f: File) => {
    setResult(null);
    setError(null);
    const lower = f.name.toLowerCase();
    if (!EXTENSIONS.some((e) => lower.endsWith(e))) {
      setFile(f);
      setError("Upload a wav, mp3, m4a or webm clip.");
      return;
    }
    if (f.size > MAX_BYTES) {
      setFile(f);
      setError(`That file is ${sizeLabel(f.size)}. The limit is 20 MB.`);
      return;
    }
    setFile(f);
    setBusy(true);
    try {
      setResult(await verifyClip(f));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not check this clip.");
    } finally {
      setBusy(false);
    }
  }, []);

  const openReceipt = (e: FormEvent) => {
    e.preventDefault();
    const id = lookup.trim();
    if (id) window.location.assign(`/verify?receipt=${encodeURIComponent(id)}`);
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files?.[0];
    if (f && !busy) void check(f);
  };

  return (
    <div
      className={`site ${siteFont.variable} ${siteSerif.variable} min-h-screen`}
      style={{ background: "var(--s-ground)" }}
    >
      <div className="border-b border-[var(--s-line)] bg-[var(--s-offwhite)]">
        <header className="s-container flex h-16 items-center gap-6">
          <Link href="/" aria-label="Vakya home" className="flex items-center">
            <Logo size={30} />
          </Link>
          <Link
            href="/desk"
            prefetch={false}
            className="s-btn s-btn--primary group ml-auto inline-flex items-center"
          >
            Open the desk
            <ArrowUpRight
              className="h-4 w-4 transition-transform duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
              aria-hidden
            />
          </Link>
        </header>
      </div>

      <section className="mx-auto max-w-[47.5rem] px-[var(--s-gutter)] pb-24 pt-10 md:pt-16">
        <h1 className="s-title max-w-[14ch]">{receiptId ? "Verify a receipt" : "Verify a clip"}</h1>
        <p className="lede mt-4 max-w-[52ch]">
          {receiptId
            ? "A receipt is Vakya's signed record of something it did. Below is how this booking was made, and whether the record still checks out."
            : "Every clip Vakya makes has a signed receipt, a content manifest and an AI label. Upload audio to see whether it came from here."}
        </p>

        {receiptId && <BookingProof receiptId={receiptId} />}

        <form onSubmit={openReceipt} className="mt-8 flex flex-wrap items-center gap-2">
          <label htmlFor="receipt-id" className="s-small text-[var(--s-ink-2)]">Have a receipt id, for a booking?</label>
          <input
            id="receipt-id"
            value={lookup}
            onChange={(e) => setLookup(e.target.value)}
            placeholder="Paste it here"
            className="h-10 min-w-[16rem] flex-1 rounded-lg border border-[var(--s-bar)] bg-[var(--s-paper)] px-3 text-[14px]"
          />
          <button type="submit" className="s-btn inline-flex items-center border border-[var(--s-bar)]" disabled={!lookup.trim()}>Show the proof</button>
        </form>

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`mt-8 rounded-[var(--s-r-card)] border border-dashed px-6 py-12 text-center transition-colors ${
            dragging
              ? "border-[var(--violet-700)] bg-[var(--violet-200)]/40"
              : "border-[var(--s-bar)] bg-[var(--s-fill)]"
          }`}
        >
          <input
            ref={inputRef}
            type="file"
            accept="audio/*,.wav,.mp3,.m4a,.webm,.flac,.ogg"
            className="sr-only"
            aria-label="Audio file to verify"
            tabIndex={-1}
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) void check(f);
            }}
          />
          {busy ? (
            <div className="flex flex-col items-center gap-3" role="status">
              <LoaderCircle
                className="h-8 w-8 animate-spin text-[var(--s-ink-2)]"
                aria-hidden
              />
              <p className="s-body text-[var(--s-ink-2)]">
                Checking {file?.name}...
              </p>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-4">
              <span className="grid h-12 w-12 place-items-center rounded-xl border border-[var(--s-line)] bg-[var(--s-paper)]">
                {file ? (
                  <FileAudio className="h-6 w-6" aria-hidden />
                ) : (
                  <Upload className="h-6 w-6" aria-hidden />
                )}
              </span>
              <p className="s-body text-[var(--s-ink-2)]">
                {file ? (
                  <>
                    <span className="font-medium text-[var(--s-ink)]">
                      {file.name}
                    </span>{" "}
                    ({sizeLabel(file.size)})
                  </>
                ) : (
                  "Drag an audio file here"
                )}
              </p>
              <button
                type="button"
                onClick={() => inputRef.current?.click()}
                className="s-btn s-btn--primary inline-flex items-center"
              >
                {file ? "Check another clip" : "Choose a file"}
              </button>
              <p className="s-small text-[var(--s-mute)]">
                wav, mp3, m4a or webm, up to 20 MB. The file is checked and not
                kept.
              </p>
            </div>
          )}
        </div>

        <div aria-live="polite" className="mt-6">
          {error && (
            <p
              role="alert"
              className="rounded-lg border border-[#fda29b] bg-[#fef3f2] px-4 py-3 s-body text-[#b42318]"
            >
              {error}
            </p>
          )}
          {result && !busy && <VerifyResult result={result} />}
        </div>

        <p className="mt-10 max-w-[64ch] s-small leading-relaxed text-[var(--s-mute)]">
          A match is evidence, not proof. It holds for the exact file Vakya made: once a clip is
          re-encoded or edited it no longer matches its receipt, and a clip with no match could still
          have been made here.
        </p>
      </section>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={null}>
      <VerifyPage />
    </Suspense>
  );
}
