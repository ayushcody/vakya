import useSWR from "swr";
import { api } from "@/lib/api";

/** GET /api/whatsapp/status (backend/routers/whatsapp.py). The token is never in it. */
export interface WhatsAppStatus {
  configured: boolean;
  access_token_set: boolean;
  phone_number_id_set: boolean;
  missing: string[];
  api_version: string;
  templates: { booked: string; moved: string; cancelled: string };
  confirmations_on: boolean;
  preview_on: boolean;
  send_delay_s: number;
  backstop_s: number;
  counts: { sent: number; preview: number; failed: number; skipped: number; pending: number; consented_numbers: number };
}

export type WhatsAppState = "sent" | "preview" | "failed" | "skipped";

/** One row of GET /api/whatsapp/messages, newest first. "preview" was built but NOT sent. */
export interface WhatsAppMessage {
  id: string;
  at: string;
  state: WhatsAppState;
  kind: "booked" | "moved" | "cancelled";
  booking_id: string;
  /** Masked by the server, e.g. ••••3210. */
  phone_masked: string;
  text: string;
  language: string;
  error: string | null;
  reason: string | null;
  source: string | null;
}

const quiet = { revalidateOnFocus: false, shouldRetryOnError: false } as const;

export function useWhatsAppStatus() {
  return useSWR<WhatsAppStatus>("/api/whatsapp/status", (p: string) => api.get<WhatsAppStatus>(p), quiet);
}

export function useWhatsAppMessages() {
  return useSWR<{ messages: WhatsAppMessage[] }>("/api/whatsapp/messages", (p: string) => api.get<{ messages: WhatsAppMessage[] }>(p), { ...quiet, refreshInterval: 15000 });
}

export const STATE_LABEL: Record<WhatsAppState, string> = {
  sent: "Sent",
  preview: "Preview, not sent",
  failed: "Failed",
  skipped: "Skipped",
};

export const KIND_LABEL: Record<WhatsAppMessage["kind"], string> = { booked: "Booked", moved: "Moved", cancelled: "Cancelled" };
