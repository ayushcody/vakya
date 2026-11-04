"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { Headphones } from "lucide-react";
import { Badge, Button, Card } from "@/components/ui";
import { api } from "@/lib/api";
import { PcmPlayer } from "@/lib/livePcm";
import { LiveMonitor } from "./LiveMonitor";
import type { LiveCall } from "./types";

function clock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** Phone calls happening right now (GET /api/phone/live), refreshed every few seconds; one request shared by everything that asks. */
export function useLiveCalls() {
  return useSWR<{ calls: LiveCall[] }>("/api/phone/live", (p: string) => api.get<{ calls: LiveCall[] }>(p), {
    refreshInterval: 3000,
    shouldRetryOnError: false,
  });
}

/**
 * Phone calls that are happening right now, with a way to listen in. Renders nothing while no call is live.
 * `listenTo` starts listening to that conversation (the details pane's "Listen in" button sets it).
 */
export function LiveCalls({ listenTo = null }: { listenTo?: { id: string; n: number } | null }) {
  const { data } = useLiveCalls();
  const [session, setSession] = useState<{ id: string; player: PcmPlayer } | null>(null);
  const calls = data?.calls ?? [];
  const listening = session?.id ?? null;

  const listen = async (c: LiveCall) => {
    session?.player.close();
    const player = new PcmPlayer();
    await player.resume();      // inside the click: the browser allows audio
    setSession({ id: c.conversation_id, player });
  };
  const stop = () => { session?.player.close(); setSession(null); };
  const asked = useRef(0);
  const target = calls.find((c) => c.conversation_id === listenTo?.id);
  useEffect(() => {
    if (!listenTo || !target || listenTo.n === asked.current) return;
    asked.current = listenTo.n;
    void listen(target);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listenTo?.n, target?.conversation_id]);

  if (calls.length === 0 && !session) return null;
  const current = calls.find((c) => c.conversation_id === listening);
  return (
    <section id="live-now" aria-label="Calls happening now" className="mb-5 space-y-2">
      <h2 className="flex items-center gap-2 text-base font-semibold text-text-primary">
        <span className="size-2 rounded-full bg-accent-success motion-safe:animate-pulse" aria-hidden />Live now
      </h2>
      {calls.map((c) => (
        <Card key={c.conversation_id} className="flex flex-wrap items-center justify-between gap-3 p-4">
          <div className="min-w-0">
            <p className="text-[15px] font-semibold text-text-primary">{c.agent_name}</p>
            <p className="text-sm text-text-secondary">
              {c.direction === "outbound" ? "Outgoing call" : "Incoming call"} · {clock(c.seconds)}
              {c.listeners > 0 && ` · ${c.listeners} listening`}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {c.human_active && <Badge tone="warning" dot>A person has the call</Badge>}
            <Button size="sm" variant="outline" onClick={() => listen(c)} disabled={listening === c.conversation_id}>
              <Headphones className="size-4" />Listen in
            </Button>
          </div>
        </Card>
      ))}
      {session && (
        <LiveMonitor
          key={session.id}
          call={current ?? { conversation_id: session.id, call_sid: null, agent_id: "", agent_name: "Call", direction: "inbound", started_at: 0, seconds: 0, human_active: false, listeners: 0 }}
          player={session.player}
          onClose={stop}
        />
      )}
    </section>
  );
}
