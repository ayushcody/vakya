import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The dev-mode route indicator sits over the sidebar avatar and shows up in live demos.
  devIndicators: false,
  // The optimizer serves AVIF to browsers that accept it and WebP otherwise (Next's default is WebP only).
  images: { formats: ["image/avif", "image/webp"] },
  async redirects() {
    return [
      // The desk replaced the dashboard; the report card replaced the proof page.
      { source: "/dashboard", destination: "/desk", permanent: true },
      { source: "/proof", destination: "/report-card", permanent: true },
    ];
  },
};

export default nextConfig;
