import "../site.css";
import { PublicMain } from "@/components/PublicMain";

/**
 * The demo film's title page at "/" (components/watch/WatchPreview.tsx). It takes the site's tokens from ../site.css
 * (scoped under `.site`) but none of the marketing shell: no site nav, no footer, no smooth scrolling. The marketing
 * landing page lives at /home.
 */
export default function WatchLayout({ children }: { children: React.ReactNode }) {
  return (
    <PublicMain>
      <div className="site">{children}</div>
    </PublicMain>
  );
}
