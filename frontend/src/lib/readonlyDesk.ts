/**
 * The read-only public desk. On the hosted site (vakya.ayushchougula.in) there is no Vakya server behind the page: every
 * GET the desk makes is answered from a snapshot of sample data (public/demo-desk.json, made by
 * scripts/snapshot_demo_desk.py with private details removed), and anything that would change something, place a call
 * or spend credits is refused with a short message. On the team's own machine nothing changes.
 *
 * On when NEXT_PUBLIC_VAKYA_READONLY=1, or when the page is served from a host that is not local.
 */
import { ApiError } from "@/lib/apiBase";

const KEEP = ["agent_id", "caller_ref", "conversation_id"];   // the query parameters that change an answer
export const READONLY_MESSAGE = "This is a read-only demo: calls, bookings and changes are switched off here.";

function localHost(host: string): boolean {
  return host === "localhost" || host === "127.0.0.1" || host === "[::1]" || host.endsWith(".localhost")
    || host.endsWith(".local") || /^(10|192\.168|172\.(1[6-9]|2\d|3[01]))\./.test(host);
}

export function readonlyDesk(): boolean {
  if ((process.env.NEXT_PUBLIC_VAKYA_READONLY || "") === "1") return true;
  if (typeof window === "undefined") return false;
  const host = window.location?.hostname;
  return Boolean(host) && !localHost(host.toLowerCase());
}

/** The snapshot key of a request: its path plus the query parameters that change the answer, sorted. */
export function snapshotKey(path: string): string {
  const u = new URL(path, "http://x");
  const kept = KEEP.map((k) => [k, u.searchParams.get(k)] as const).filter(([, v]) => v !== null) as [string, string][];
  kept.sort(([a], [b]) => a.localeCompare(b));
  const q = new URLSearchParams(kept).toString();
  return u.pathname + (q ? `?${q}` : "");
}

let snapshot: Promise<Record<string, unknown>> | null = null;
function load(): Promise<Record<string, unknown>> {
  if (!snapshot) {
    snapshot = fetch("/demo-desk.json").then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
  }
  return snapshot;
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** The answer to a request in the read-only desk, as a Response. Writes are refused; unknown reads are a 404. */
export async function readonlyResponse(method: string, path: string): Promise<Response> {
  if (method !== "GET") throw new ApiError(READONLY_MESSAGE, 403, "read_only");
  const rel = path.replace(/^https?:\/\/[^/]+/, "");
  if (/\/events(\?|$)/.test(rel)) {   // a long poll: answer nothing new, slowly, so the page does not spin
    await wait(15000);
    return new Response(JSON.stringify({ events: [], seq: 0 }), { status: 200, headers: { "Content-Type": "application/json" } });
  }
  const data = await load();
  const key = snapshotKey(rel);
  const hit = key in data ? data[key] : data[new URL(rel, "http://x").pathname];
  if (hit === undefined) {
    if (typeof window !== "undefined") {   // which requests the snapshot is missing (scripts/snapshot_demo_desk.py)
      const w = window as unknown as { __vakyaMissing?: string[] };
      (w.__vakyaMissing ??= []).includes(rel) || w.__vakyaMissing.push(rel);
    }
    throw new ApiError("Not part of the demo.", 404, "not_found");
  }
  return new Response(JSON.stringify(hit), { status: 200, headers: { "Content-Type": "application/json" } });
}
