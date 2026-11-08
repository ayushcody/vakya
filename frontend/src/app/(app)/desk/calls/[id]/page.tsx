"use client";

import { use } from "react";
import { PageHeader } from "@/components/ui";
import { CallReplay } from "@/components/live/CallReplay";

/** One call in the live call view: /desk/calls/{conversation id}. */
export default function CallViewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <PageHeader width="wide" title="Call" description="What was said, what the front desk did about it, and where the confirmations went.">
      <CallReplay id={id} />
    </PageHeader>
  );
}
