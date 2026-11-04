/**
 * @jest-environment jsdom
 */
import { api } from "@/lib/api";

let session: { access_token: string } | null = null;
jest.mock("@/lib/supabase", () => ({
  createClient: () => ({
    auth: { getSession: async () => ({ data: { session } }) },
  }),
}));

const ok = () => ({
  ok: true,
  status: 200,
  headers: new Headers(),
  json: async () => ({}),
  text: async () => "{}",
});
const sentAuth = () =>
  (global.fetch as jest.Mock).mock.calls[0][1].headers.Authorization;

describe("api Authorization header", () => {
  const original = process.env.NEXT_PUBLIC_VAKYA_LOCAL_TOKEN;
  beforeEach(() => {
    session = null;
    localStorage.clear();
    global.fetch = jest.fn().mockResolvedValue(ok());
  });
  afterEach(() => {
    if (original === undefined)
      delete process.env.NEXT_PUBLIC_VAKYA_LOCAL_TOKEN;
    else process.env.NEXT_PUBLIC_VAKYA_LOCAL_TOKEN = original;
  });

  it("sends the Supabase session token when signed in, even if a demo token is set", async () => {
    process.env.NEXT_PUBLIC_VAKYA_LOCAL_TOKEN = "demo-token";
    session = { access_token: "sess" };
    await api.get("/x");
    expect(sentAuth()).toBe("Bearer sess");
  });

  it("falls back to the demo token when there is no session", async () => {
    process.env.NEXT_PUBLIC_VAKYA_LOCAL_TOKEN = "demo-token";
    await api.get("/x");
    expect(sentAuth()).toBe("Bearer demo-token");
  });

  it("sends no Authorization header with neither", async () => {
    delete process.env.NEXT_PUBLIC_VAKYA_LOCAL_TOKEN;
    await api.get("/x");
    expect(sentAuth()).toBeUndefined();
  });
});
