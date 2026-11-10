import type { Metadata } from "next";
import { WatchPreview } from "@/components/watch/WatchPreview";

const TITLE = "Vakya: call once, it's booked";
const DESCRIPTION =
  "Watch Vakya, a voice front desk for Indian businesses built on Gnani Prisma and Timbre, take a real phone call: it checks the schedule, reads every detail back, books on a spoken yes and confirms on WhatsApp.";

export const metadata: Metadata = {
  title: { absolute: TITLE },
  description: DESCRIPTION,
  openGraph: {
    type: "video.other",
    siteName: "Vakya",
    title: TITLE,
    description: DESCRIPTION,
    images: [{ url: "/preview/poster.jpg", width: 1920, height: 1080, alt: "Vakya, an AI front desk that talks like your receptionist" }],
    videos: [{ url: "/landing/demo.mp4", type: "video/mp4", width: 1920, height: 1080 }],
  },
  alternates: { canonical: "/demo" },
  twitter: { card: "summary_large_image", title: TITLE, description: DESCRIPTION, images: ["/preview/poster.jpg"] },
};

export default function WatchPage() {
  return <WatchPreview />;
}
