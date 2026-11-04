/**
 * Deprecated shims. The marketing site no longer loads its own Latin faces: Inter (--nf-body, next/font) is set on <html>
 * and Gambarino (--nf-display, Fontshare link) is loaded by the root layout, and `.site` in globals.css re-exports them as
 * --s-font-body and --s-font-display. The exports and their `variable` stay so layouts that interpolate them keep
 * compiling; they add no class and download nothing.
 */
export const siteFont = { variable: "" } as const;
export const siteSerif = { variable: "" } as const;
