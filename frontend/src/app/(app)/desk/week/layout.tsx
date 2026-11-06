import type { Metadata } from "next";

export const metadata: Metadata = { title: "This week · Vakya" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
