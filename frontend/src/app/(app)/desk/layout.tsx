import type { Metadata } from "next";
import { ErrorBoundary } from "@/components/ui";

// Today. Every page below sets its own title in its own layout.tsx, which overrides this one.
export const metadata: Metadata = { title: "Today · Vakya" };

export default function DeskLayout({ children }: { children: React.ReactNode }) {
  return <ErrorBoundary>{children}</ErrorBoundary>;
}
