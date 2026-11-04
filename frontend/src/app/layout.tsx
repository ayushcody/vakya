import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { ALL_SITE_PAGES } from "@/components/site/routes";

// This root layout is shared by every route, so it stays minimal: the two Vakya faces (Inter for text, Gambarino for
// display), no auth, sidebar or toaster. Those live in the route groups: app/(app)/layout.tsx (the desk and the
// auth pages) and the public layouts (site, verify).

/** Body and UI face: Inter 400/500/600, self-hosted by next/font. Its variable is --nf-body, declared on <html>. */
const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--nf-body",
  display: "swap",
});

/** Display face: Gambarino 400 from Fontshare (free licence). --nf-display in globals.css names it. */
const GAMBARINO_CSS = "https://api.fontshare.com/v2/css?f[]=gambarino@400&display=swap";

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#f8f8f8",
};

export const metadata: Metadata = {
  // Absolute URLs for Open Graph / Twitter images. Set NEXT_PUBLIC_SITE_URL in production.
  // The icons come from app/icon.svg, app/apple-icon.png and app/favicon.ico, and the share card from app/opengraph-image.tsx.
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3010"),
  title: "Vakya: call once, it's booked",
  description:
    "A voice front desk for Indian clinics, hotels, salons and other small businesses, built on Gnani Prisma and Timbre. It answers in Hindi, Marathi and English, checks the schedule, reads every number back, books only on a spoken yes and leaves a signed receipt.",
  keywords: ["voice agent", "appointment booking", "Hindi", "Marathi", "code-mixed speech", "Gnani", "Prisma", "Timbre", "telephony", "Vakya"],
  openGraph: {
    type: "website",
    siteName: "Vakya",
    title: "Vakya: call once, it's booked",
    description: "A voice front desk for Indian clinics and small businesses that reads every number back and books only on a spoken yes. Built on Gnani Prisma and Timbre.",
  },
  twitter: {
    card: "summary_large_image",
    title: "Vakya: call once, it's booked",
    description: "A voice front desk for Indian clinics and small businesses that reads every number back and books only on a spoken yes.",
  },
};

/** Runs before first paint so the saved theme never flashes. Defaults to light, matching the marketing site. */
const themeScript = `(function(){try{var t=localStorage.getItem('vakya-theme');if(t!=='light'&&t!=='dark'){t='light';}document.documentElement.setAttribute('data-theme',t);}catch(e){document.documentElement.setAttribute('data-theme','light');}})();`;

/** Before first paint on the marketing pages (the list in components/site/routes): hide the hero and [data-reveal] elements via CSS (html.reveal-on) so server-rendered text
 *  never flashes and then snaps away when GSAP starts. Skipped under reduced motion and without JS (everything shows).
 *  SmoothScroll sets window.__vakyaMotion; if hydration never gets there, the class is dropped after 6 s. */
const SITE_PATHS = JSON.stringify(ALL_SITE_PAGES.map((p) => p.href));
const revealScript = `(function(){try{var d=document.documentElement;var p=location.pathname;if(p.length>1&&p.slice(-1)==="/")p=p.slice(0,-1);if(${SITE_PATHS}.indexOf(p)<0||window.matchMedia("(prefers-reduced-motion: reduce)").matches)return;d.classList.add("reveal-on");setTimeout(function(){if(!window.__vakyaMotion)d.classList.remove("reveal-on");},6000);}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="light" className={inter.variable} suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://api.fontshare.com" />
        <link rel="preconnect" href="https://cdn.fontshare.com" crossOrigin="anonymous" />
        <link rel="stylesheet" href={GAMBARINO_CSS} />
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        <script dangerouslySetInnerHTML={{ __html: revealScript }} />
      </head>
      <body className="antialiased">{children}</body>
    </html>
  );
}
