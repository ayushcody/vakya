import type { MetadataRoute } from "next";

/**
 * The desk as an app on the owner's phone (Add to Home screen). It opens straight on Today, full screen, in the desk's
 * own colours. No offline copy: the desk always shows what the server has now. Contract: docs/DESK_FEATURES.md.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/desk",
    name: "Vakya desk",
    short_name: "Vakya",
    description: "Your front desk on your phone: today's appointments, calls and a ping for every new booking.",
    start_url: "/desk",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#f8f8f8",
    theme_color: "#ffffff",
    categories: ["business", "productivity"],
    icons: [
      { src: "/brand/vakya-app-icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/brand/vakya-app-icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/brand/vakya-app-icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
    ],
    shortcuts: [
      { name: "Bookings", url: "/desk/bookings" },
      { name: "Calls", url: "/desk/calls" },
      { name: "This week", url: "/desk/week" },
    ],
  };
}
