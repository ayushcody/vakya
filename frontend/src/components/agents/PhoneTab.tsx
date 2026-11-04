"use client";

import Link from "next/link";
import { Phone, PhoneIncoming, Settings } from "lucide-react";
import { Badge, Button } from "@/components/ui";
import type { Agent } from "@/types/api";
import { MissedCallPanel } from "./MissedCallPanel";
import { usePhoneConnected } from "./agentStatus";

interface Props {
  agent: Agent;
  onPhone?: (agent: Agent) => void;
}

/** The "Phone number" tab: let people ring the business number and reach the receptionist, or have it phone someone. */
export function PhoneTab({ agent, onPhone }: Props) {
  const phone = usePhoneConnected();

  return (
    <section aria-labelledby="phone-title" className="space-y-4 rounded-2xl border border-border bg-bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
        <div>
          <h2 id="phone-title" className="font-display text-lg font-semibold text-text-primary">Phone number</h2>
          <p className="mt-1 text-sm text-text-secondary">
            Let people ring your number and talk to the front desk, or have it call someone back after a missed call.
          </p>
        </div>
        {phone === true && <Badge tone="success" dot>Phone line connected</Badge>}
        {phone === false && <Badge tone="warning" dot>Phone line not connected</Badge>}
      </div>
      <div className="flex flex-wrap gap-2">
        {onPhone && (
          <Button onClick={() => onPhone(agent)} leftIcon={<PhoneIncoming className="size-4" />}>
            Receive calls on your number
          </Button>
        )}
        {onPhone && (
          <Button variant="outline" onClick={() => onPhone(agent)} leftIcon={<Phone className="size-4" />}>
            Phone someone
          </Button>
        )}
      </div>
      {phone === false && (
        <p className="text-sm text-text-secondary">
          Your phone line is not connected yet. Open the setup above for the steps, or ask the person who installed Vakya.
        </p>
      )}
      <MissedCallPanel agentId={agent.id} />
      <Link
        href="/desk/settings"
        className="inline-flex min-h-10 items-center gap-2 text-sm font-medium text-accent-primary underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-accent-primary"
      >
        <Settings className="size-4" aria-hidden /> Phone provider and limits are in Settings
      </Link>
    </section>
  );
}
