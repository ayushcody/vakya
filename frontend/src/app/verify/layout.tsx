import type { Metadata } from "next";
import "../site.css";
import { PublicMain } from "@/components/PublicMain";
import { manrope } from "@/lib/brandFont";

export const metadata: Metadata = {
  title: "Verify a clip - Vakya",
  description: "Upload an audio clip to check whether it was made with Vakya: a signed receipt and a content manifest cover every clip.",
};

/** Public and lean: no sign-in, no toaster. site.css gives /verify the Vakya look the marketing pages have. page-in is the route entrance the root template used to give it. */
export default function VerifyLayout({ children }: { children: React.ReactNode }) {
  return (
    <PublicMain>
      <div className={`page-in ${manrope.variable}`}>{children}</div>
    </PublicMain>
  );
}
