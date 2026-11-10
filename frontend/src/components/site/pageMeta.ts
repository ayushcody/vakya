import type { Metadata } from "next";

const SITE_NAME = "Vakya";

/** Metadata for one marketing page: a unique title and description, a canonical path and matching social cards.
 *  The title is "<page> | Vakya"; Home keeps its own exact title in app/(site)/page.tsx. */
export function pageMetadata({ title, description, path }: { title: string; description: string; path: string }): Metadata {
  const full = `${title} | ${SITE_NAME}`;
  return {
    title: { absolute: full },
    description,
    alternates: { canonical: path },
    openGraph: {
      type: "website",
      siteName: SITE_NAME,
      title: full,
      description,
      url: path,
      // The 1200x630 card rendered by app/opengraph-image.tsx. Named here because a page-level openGraph object replaces the file-based one.
      images: [{ url: "/opengraph-image", width: 1200, height: 630, alt: SITE_NAME }],
    },
    twitter: { card: "summary_large_image", title: full, description, images: ["/opengraph-image"] },
  };
}
