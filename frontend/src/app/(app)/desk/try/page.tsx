"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Bot, PhoneOutgoing } from "lucide-react";
import { Button, Card, EmptyState, ErrorBoundary, ErrorState, PageHeader, Skeleton } from "@/components/ui";
import { useAgents } from "@/hooks/useApi";
import { CallDialog } from "@/components/agents/CallDialog";
import { WebCallPanel } from "@/components/agents/WebCallPanel";
import { LiveCall } from "@/components/live/LiveCall";

function TryContent() {
  const agents = useAgents();
  const params = useSearchParams();
  const wanted = params?.get("agent") ?? null;
  const [picked, setPicked] = useState<string | null>(null);
  const [callOpen, setCallOpen] = useState(false);

  const list = agents.data ?? [];
  const agent = list.find((a) => a.id === (picked ?? wanted)) ?? list[0] ?? null;

  return (
    <PageHeader
      width="full"
      title="Try a call"
      description="Call the front desk from your phone and watch it work: what is said on the left, the schedule on the right. Practice calls show up on the Calls page, marked as practice."
    >
      {agents.error && <ErrorState title="Could not load the front desk" message={agents.error.message} onRetry={() => agents.mutate()} />}
      {agents.isLoading && <Skeleton className="h-40 w-full" />}
      {agents.data && list.length === 0 && (
        <EmptyState
          icon={<Bot />}
          title="No front desk to try yet"
          description="Set one up from a template first. It takes a minute."
          action={<Link href="/desk/receptionist"><Button size="sm">Set up a front desk</Button></Link>}
        />
      )}
      {agent && (
        <div className="space-y-6">
          {list.length > 1 && (
            <label className="flex items-center gap-2 text-sm text-text-secondary">
              <span>Front desk</span>
              <select
                value={agent.id}
                onChange={(e) => setPicked(e.target.value)}
                className="h-10 min-w-0 rounded-lg border border-border bg-bg-surface px-3 text-sm text-text-primary"
              >
                {list.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          <LiveCall key={`live-${agent.id}`} agentId={agent.id} />

          <WebCallPanel key={`webcall-${agent.id}`} agentId={agent.id} />

          <Card className="space-y-4">
            <div className="border-b border-border pb-4">
              <h2 className="flex items-center gap-2 font-display text-lg font-semibold text-text-primary">
                <PhoneOutgoing className="size-4 text-accent-primary" aria-hidden />
                Have it call you
              </h2>
              <p className="mt-1 text-sm text-text-secondary">
                The front desk rings a number you choose, once the person on it has agreed, and you can listen in from the Calls page.
              </p>
            </div>
            <Button onClick={() => setCallOpen(true)} leftIcon={<PhoneOutgoing className="size-4" />}>
              Call me
            </Button>
          </Card>
          <CallDialog agent={agent} open={callOpen} onClose={() => setCallOpen(false)} initialTab="phone" />
        </div>
      )}
    </PageHeader>
  );
}

export default function TryPage() {
  return (
    <ErrorBoundary>
      {/* useSearchParams needs a Suspense boundary for static rendering */}
      <Suspense fallback={null}>
        <TryContent />
      </Suspense>
    </ErrorBoundary>
  );
}
