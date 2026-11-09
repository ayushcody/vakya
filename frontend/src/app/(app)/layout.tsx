import type { Viewport } from "next";
import { Suspense } from "react";
import { Toaster } from "sonner";
import { AuthProvider } from "@/components/AuthProvider";
import { DemoTour } from "@/components/DemoTour";
import { LayoutWrapper } from "@/components/LayoutWrapper";
import { APP_FONT_CLASSES } from "@/lib/appFonts";
import { manrope } from "@/lib/brandFont";

/** The desk runs edge to edge as an installed phone app (the tab bar clears the home indicator itself); white bars match its top bar. */
export const viewport: Viewport = { viewportFit: "cover", themeColor: "#ffffff" };

/**
 * Shell of the app: the desk (sidebar, command palette), the auth pages, auth state and toasts. The public site and
 * /verify do not render this layout, so none of this code ships to them. The app fonts are declared on this wrapper
 * (lib/appFonts.ts for Geist, lib/brandFont.ts for the Manrope wordmark).
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className={`${APP_FONT_CLASSES} ${manrope.variable}`}>
      <AuthProvider>
        <LayoutWrapper>{children}</LayoutWrapper>
        <Suspense fallback={null}><DemoTour /></Suspense>
      </AuthProvider>
      <Toaster position="top-right" richColors closeButton theme="system" />
    </div>
  );
}
