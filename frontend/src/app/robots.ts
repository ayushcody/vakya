import type { MetadataRoute } from "next";

/** The marketing pages are public; the signed-in app is not worth indexing. */
export default function robots(): MetadataRoute.Robots {
  const base = (process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3010").replace(/\/+$/, "");
  return {
    rules: [{ userAgent: "*", allow: "/", disallow: ["/desk", "/login", "/register"] }],
    sitemap: `${base}/sitemap.xml`,
  };
}
