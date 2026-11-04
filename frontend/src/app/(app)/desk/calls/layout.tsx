import type { Metadata } from "next";

export const metadata: Metadata = { title: "Calls · Vakya" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
