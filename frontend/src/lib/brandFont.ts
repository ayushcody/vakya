/**
 * Deprecated shim. The wordmark used to load Manrope here; it is now set in Gambarino, which the root layout loads from
 * Fontshare (--nf-display). The export and its `variable` stay so existing layouts that interpolate it keep compiling;
 * it adds no class and downloads nothing.
 */
export const manrope = { variable: "" } as const;
