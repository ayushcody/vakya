import { render, screen, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import { WhatsAppSettings } from "@/components/whatsapp/WhatsAppSettings";
import { api } from "@/lib/api";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() }, ApiError: class ApiError extends Error {}, API_URL: "http://api.test" }));
const get = api.get as jest.Mock;

const status = (over = {}) => ({
  configured: false, access_token_set: false, phone_number_id_set: false, missing: ["WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"],
  api_version: "v27.0", templates: { booked: "vakya_booking_confirmed", moved: "vakya_booking_moved", cancelled: "vakya_booking_cancelled" },
  confirmations_on: true, preview_on: true, send_delay_s: 30, backstop_s: 300,
  counts: { sent: 0, preview: 1, failed: 0, skipped: 0, pending: 0, consented_numbers: 1 }, ...over,
});
const msgs = [
  { id: "a", at: "2026-11-05T09:30:00+05:30", state: "preview", kind: "booked", booking_id: "A12", phone_masked: "••••3210", text: "Hello! Your booking at Clinic for Ravi is confirmed: Fri 6 Nov, 3:00 pm. Booking ID: A12. To change or cancel it, just call us again.", language: "en", error: null, reason: null, source: "agent" },
  { id: "b", at: "2026-11-05T09:31:00+05:30", state: "failed", kind: "moved", booking_id: "A13", phone_masked: "••••1111", text: "", language: "en", error: "WhatsApp refused the message (code 132001)", reason: null, source: "agent" },
];
const view = () => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}><WhatsAppSettings /></SWRConfig>);

describe("Settings: WhatsApp confirmations", () => {
  it("says it is not set up, what is missing, the template names, and labels a preview as not sent", async () => {
    get.mockReset().mockImplementation(async (path: string) => (path.endsWith("/status") ? status() : { messages: msgs }));
    view();
    expect(await screen.findByText(/Not set up: previews only/)).toBeInTheDocument();
    expect(screen.getByText("WHATSAPP_ACCESS_TOKEN")).toBeInTheDocument();
    expect(screen.getByText("vakya_booking_moved")).toBeInTheDocument();
    const list = await screen.findByTestId("whatsapp-messages");
    expect(within(list).getByText("Preview, not sent")).toBeInTheDocument();
    expect(within(list).getByText("••••3210")).toBeInTheDocument();
    expect(within(list).getByText("Failed")).toBeInTheDocument();
    expect(within(list).getByText(/code 132001/)).toBeInTheDocument();
    expect(within(list).queryByText("Sent")).toBeNull();
  });

  it("shows set up when configured and an empty list when there are no messages", async () => {
    get.mockReset().mockImplementation(async (path: string) => (path.endsWith("/status") ? status({ configured: true, missing: [] }) : { messages: [] }));
    view();
    expect(await screen.findByText("Set up")).toBeInTheDocument();
    expect(await screen.findByText(/None yet/)).toBeInTheDocument();
  });
});
