import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The dev-mode route indicator sits over the sidebar avatar and shows up in live demos.
  devIndicators: false,
  // Dev only: lets the desk be opened at 0.0.0.0, which the app treats as "not local" (the read-only public desk).
  allowedDevOrigins: ["0.0.0.0"],
  // The optimizer serves AVIF to browsers that accept it and WebP otherwise (Next's default is WebP only).
  images: { formats: ["image/avif", "image/webp"] },
  async redirects() {
    return [
      // The desk replaced the dashboard; the report card replaced the proof page.
      { source: "/dashboard", destination: "/desk", permanent: true },
      { source: "/proof", destination: "/report-card", permanent: true },
      // The landing page was briefly at /home while the demo film had "/"; the film is now at /demo.
      { source: "/home", destination: "/", permanent: false },
    ];
  },
};

export default nextConfig;
