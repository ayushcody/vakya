import { SmoothScroll } from "@/components/site/SmoothScroll";
import { SiteNav } from "@/components/site/SiteNav";
import { Footer } from "@/components/site/Ending";
import "../site.css";
import { PublicMain } from "@/components/PublicMain";
import { manrope } from "@/lib/brandFont";

/**
 * Shared shell of the public marketing pages (/, /how-it-works, /report-card, /run-it, /faq). It stays mounted
 * while a visitor moves between them, so the nav, the smooth scrolling and the footer are not rebuilt on every page.
 * PublicMain provides the skip link and <main id="main-content">, as the app shell does for the desk. The shared
 * skip link is redirected to #content by SmoothScroll so it lands after the nav. The look comes from ../site.css (the
 * Vakya design): Gambarino for headings, loaded here from Fontshare by a <link>, and Inter,
 * which the root wires as --nf-body. Manrope is here for the wordmark only. /verify, the app (the desk,
 * auth) and the rest of the root keep their own layouts and load none of the desk code.
 */
export default function SiteLayout({ children }: { children: React.ReactNode }) {
  return (
    <PublicMain>
      <SmoothScroll>
        {/* Gambarino (Indian Type Foundry, Fontshare free licence), weight 400 only. The page still reads with the serif fallback while it loads. */}
        <div className={`site ${manrope.variable} min-h-screen`}>
          <SiteNav />
          <div id="content">{children}</div>
          <Footer />
        </div>
      </SmoothScroll>
    </PublicMain>
  );
}
