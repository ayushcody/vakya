import { ImageResponse } from "next/og";

// The share card for links to the site (Open Graph and, through the same file, Twitter). Rendered at build time from
// this code only: no network fetches, and the default font bundled with next/og. The mark and lockup are the same
// geometry as components/Logo.tsx (64-unit mark, 8-unit stroke), inlined as data URIs because this file cannot import
// a CSS module.
export const alt = "Vakya: call once, it's booked.";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const INK = "#0b0b0f";
const PAPER = "#fcf7f2";
const ACCENT = "#6c62ed";

const BARS: ReadonlyArray<readonly [number, number]> = [
  [0, 38],
  [14, 50],
  [28, 64],
  [14, 50],
  [0, 38],
];

function markInner(): string {
  const bars = BARS.map(
    ([t, b], i) => `<rect x="${i * 13}" y="${t}" width="8" height="${b - t}" rx="4" fill="${INK}"/>`,
  ).join("");
  return `${bars}<rect x="71" y="0" width="8" height="64" rx="4" fill="${ACCENT}"/>`;
}

const LETTERS =
  `<g fill="none" stroke="${INK}" stroke-width="8" stroke-linecap="round" stroke-linejoin="round">` +
  `<path d="M0 28L14 60L28 28"/><circle cx="58" cy="44" r="16"/><path d="M74 28V60"/>` +
  `<path d="M92 4V60M113 28L92 45L114 60"/><path d="M129 28L143 60M157 28L132.5 84"/>` +
  `<circle cx="189" cy="44" r="16"/><path d="M205 28V60"/></g>`;

const uri = (svg: string) => `data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`;

const MARK = uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 79 64" width="79" height="64">${markInner()}</svg>`);
const LOCKUP = uri(
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 314 88" width="314" height="88">${markInner()}<g transform="translate(105 0)">${LETTERS}</g></svg>`,
);

export default function OpengraphImage() {
  const lockupH = 64;
  const markH = 330;
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: PAPER,
          color: INK,
          padding: "64px 80px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <img src={LOCKUP} width={Math.round((lockupH * 314) / 88)} height={lockupH} alt="" />
          <div style={{ display: "flex", fontSize: 24, color: "#6b6b6b" }}>Built on Gnani Prisma + Timbre</div>
        </div>

        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between" }}>
          <div style={{ display: "flex", flexDirection: "column", maxWidth: 640 }}>
            <div style={{ display: "flex", fontSize: 92, fontWeight: 700, lineHeight: 1.02, letterSpacing: -2 }}>
              Call once, it&apos;s booked.
            </div>
            <div style={{ display: "flex", marginTop: 28, fontSize: 28, lineHeight: 1.35, color: "#333333" }}>
              A phone front desk for clinics, hotels and small businesses. It reads every number back and commits only on a spoken yes.
            </div>
          </div>
          <img src={MARK} width={Math.round((markH * 79) / 64)} height={markH} alt="" />
        </div>
      </div>
    ),
    { ...size },
  );
}
