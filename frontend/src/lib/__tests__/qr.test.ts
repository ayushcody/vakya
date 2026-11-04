import { writeFileSync } from "fs";
import { qrModules } from "../qr";

const SAMPLES = [
  "hi",
  "https://example.com/call/awc_0123456789abcdef0123456789abcdef0123456789abcdef",
  "https://abc-def-ghi.trycloudflare.com/call/awc_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "x".repeat(120),
  "y".repeat(200),
];

describe("qrModules", () => {
  it("makes a square with the three finder patterns and a size that grows with the text", () => {
    const small = qrModules("hi");
    const big = qrModules(SAMPLES[2]);
    expect(small).toHaveLength(21);
    expect(small.every((r) => r.length === 21)).toBe(true);
    expect(big.length).toBeGreaterThan(small.length);
    expect((big.length - 17) % 4).toBe(0);
    for (const [x, y] of [[0, 0], [big.length - 7, 0], [0, big.length - 7]]) {
      expect([0, 1, 2, 3, 4, 5, 6].every((i) => big[y][x + i] && big[y + 6][x + i] && big[y + i][x] && big[y + i][x + 6])).toBe(true);
      expect(big[y + 3][x + 3]).toBe(true);
      expect(big[y + 1][x + 1]).toBe(false);
    }
  });

  it("refuses text that does not fit", () => {
    expect(() => qrModules("z".repeat(300))).toThrow();
  });

  it("is deterministic", () => {
    expect(qrModules(SAMPLES[1])).toEqual(qrModules(SAMPLES[1]));
  });

  if (process.env.QR_DUMP) {
    it("dumps matrices for an external decoder", () => {
      writeFileSync(process.env.QR_DUMP as string, JSON.stringify(SAMPLES.map((t) => ({ text: t, m: qrModules(t).map((r) => r.map(Number)) }))));
    });
  }
});
