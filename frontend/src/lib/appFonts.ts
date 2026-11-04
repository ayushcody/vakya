import { Geist_Mono } from "next/font/google";

// The app shell's faces. Inter (body, --nf-body) is loaded once in the root layout and Gambarino (display, --nf-display)
// comes from Fontshare through a <link> there, so every route gets them; this module only adds the monospace face.
// preload: false, so a face downloads only when text uses it (the public site never uses Geist Mono).

export const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
  display: "swap",
  preload: false,
});

/**
 * Classes for an element whose subtree needs the app fonts. The variable only resolves on the element that declares it and
 * its descendants, so put this on the wrapper of the subtree, and on any portal root (portals render into document.body,
 * outside the wrapper).
 */
export const APP_FONT_CLASSES = `${geistMono.variable} font-sans`;
