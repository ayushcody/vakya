import type { Metadata } from "next";

export const metadata: Metadata = { title: "Caller Theatre · Vakya" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
