import { render, screen } from "@testing-library/react";
import { VerifyResult } from "@/components/verify/VerifyResult";
import type { PublicReceipt, VerifyResponse } from "@/components/verify/types";

const receipt: PublicReceipt = {
  receipt_id: "5b1f7d3e-0000-4000-8000-000000000001",
  kind: "generation",
  created_at: "2026-10-14T10:30:00+00:00",
  engine: "timbre_clone",
  voice_id: "v1",
  voice_name: "Asha",
  consent_id: "c1",
  consent_verified: true,
  signature_valid: true,
};

const base: VerifyResponse = {
  manifest: { present: true, generator: "Vakya" },
  match: { exact: true, receipt, signature_valid: true },
  verdict: "made_with_vakya",
};

describe("VerifyResult", () => {
  it("shows the verdict, manifest note and receipt details for an exact match", () => {
    render(<VerifyResult result={base} />);
    expect(screen.getByRole("heading", { name: "Made with Vakya" })).toBeInTheDocument();
    expect(screen.getByTestId("manifest-note")).toHaveTextContent("C2PA content manifest from Vakya");
    expect(screen.getByText("Consent verified")).toBeInTheDocument();
    expect(screen.getByText("Asha")).toBeInTheDocument();
    expect(screen.getByText("Gnani Timbre (voice clone)")).toBeInTheDocument();
    expect(screen.getByText("Exact copy of the original file")).toBeInTheDocument();
    expect(screen.getByText(/Valid: the receipt has not been altered/)).toBeInTheDocument();
  });

  it("says so plainly when the signature does not verify", () => {
    render(<VerifyResult result={{ ...base, match: { ...base.match, receipt: { ...receipt, signature_valid: false } } }} />);
    expect(screen.getByText(/Invalid: the receipt does not verify/)).toBeInTheDocument();
  });

  it("does not claim consent for a preset voice", () => {
    const preset = { ...receipt, voice_id: null, consent_id: null, consent_verified: null, voice_name: "af_heart", engine: "timbre" };
    render(<VerifyResult result={{ ...base, match: { ...base.match, exact: false, receipt: preset } }} />);
    expect(screen.queryByText("Consent verified")).not.toBeInTheDocument();
    expect(screen.getByText("Preset voice (no cloning)")).toBeInTheDocument();
    expect(screen.getByText("Not an exact copy")).toBeInTheDocument();
  });

  it("reports a clip with no receipt match without implying it is genuine", () => {
    render(
      <VerifyResult
        result={{
          manifest: null,
          match: { exact: false, receipt: null, signature_valid: false },
          verdict: "not_detected",
        }}
      />,
    );
    expect(screen.getByRole("heading", { name: "Not detected" })).toBeInTheDocument();
    expect(screen.getByText(/does not show the clip is genuine/)).toBeInTheDocument();
    expect(screen.queryByTestId("receipt")).not.toBeInTheDocument();
  });

  it("says the voice owner withdrew consent, while keeping the receipt", () => {
    const withdrawn = { ...receipt, voice_withdrawn_at: "2026-10-20T09:00:00+00:00", voice_removed_reason: "withdrawn" as const };
    render(<VerifyResult result={{ ...base, match: { ...base.match, receipt: withdrawn } }} />);
    expect(screen.getByTestId("voice-withdrawn")).toHaveTextContent("Voice withdrawn");
    expect(screen.getByText(/The voice owner withdrew consent on/)).toBeInTheDocument();
    expect(screen.getByText("Consent verified when made, now withdrawn")).toBeInTheDocument();
    expect(screen.getByText(/Valid: the receipt has not been altered/)).toBeInTheDocument();
  });

  it("does not say consent was withdrawn when the voice was only deleted at the end of its retention period", () => {
    const expired = { ...receipt, voice_withdrawn_at: "2027-10-20T09:00:00+00:00", voice_removed_reason: "retention_expired" as const };
    render(<VerifyResult result={{ ...base, match: { ...base.match, receipt: expired } }} />);
    expect(screen.getByText(/when its retention period ended/)).toBeInTheDocument();
    expect(screen.queryByText(/withdrew consent on/)).not.toBeInTheDocument();
  });

  it("shows no withdrawal note for a voice that still exists", () => {
    render(<VerifyResult result={base} />);
    expect(screen.queryByTestId("voice-withdrawn")).not.toBeInTheDocument();
  });
});
