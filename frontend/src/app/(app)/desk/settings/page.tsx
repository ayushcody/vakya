"use client";

import type { ReactNode } from "react";
import useSWR from "swr";
import { CheckCircle2, CircleAlert, CircleDashed } from "lucide-react";
import { Badge, Card, ErrorBoundary, PageHeader, Skeleton } from "@/components/ui";
import type { BadgeTone } from "@/components/ui/Badge";
import { useConsentMode, useEngines, useHealth } from "@/hooks/useApi";
import { HearingSettings } from "@/components/hearing/HearingSettings";
import { WhatsAppSettings } from "@/components/whatsapp/WhatsAppSettings";
import { SpeechModelsCard } from "@/components/usage/SpeechModelsCard";
import { UsageTiles } from "@/components/usage/UsageTiles";
import { PhoneDeskSettings } from "@/components/phone/PhoneDeskSettings";
import { api } from "@/lib/api";
import { llmLabel } from "@/lib/llm";
import type { PhoneStatus, PrivacyAttestation } from "@/types/api";

const quiet = { revalidateOnFocus: false, shouldRetryOnError: false } as const;

function Row({ label, children, note }: { label: string; children: ReactNode; note?: ReactNode }) {
  return (
    <div className="grid gap-1 py-3 sm:grid-cols-[13rem_minmax(0,1fr)] sm:gap-4">
      <dt className="text-sm text-text-secondary">{label}</dt>
      <dd className="min-w-0 text-sm text-text-primary">
        {children}
        {note && <p className="mt-1 text-xs text-text-secondary">{note}</p>}
      </dd>
    </div>
  );
}

function Status({ tone, children }: { tone: BadgeTone; children: ReactNode }) {
  const Icon = tone === "success" ? CheckCircle2 : tone === "warning" || tone === "error" ? CircleAlert : CircleDashed;
  return (
    <Badge tone={tone} dot>
      <Icon className="size-3" aria-hidden /> {children}
    </Badge>
  );
}

function Section({ id, title, intro, children }: { id: string; title: string; intro: string; children: ReactNode }) {
  return (
    <Card aria-labelledby={id} className="space-y-1">
      <div className="border-b border-border pb-4">
        <h2 id={id} className="font-display text-lg font-semibold text-text-primary">{title}</h2>
        <p className="mt-1 text-sm text-text-secondary">{intro}</p>
      </div>
      <dl className="divide-y divide-border">{children}</dl>
    </Card>
  );
}

const code = "rounded bg-bg-elevated px-1.5 py-0.5 font-mono text-xs";

function SettingsContent() {
  const health = useHealth();
  const engines = useEngines();
  const consent = useConsentMode();
  const phone = useSWR<PhoneStatus>("/api/phone/status", (p: string) => api.get<PhoneStatus>(p), quiet);
  const attestation = useSWR<PrivacyAttestation>("/api/privacy/attestation", (p: string) => api.get<PrivacyAttestation>(p), quiet);

  const timbre = (engines.data ?? []).find((e) => e.id === "timbre");
  const gnaniKnown = !!engines.data && !!timbre;
  const gnaniOn = timbre?.available === true;
  const llm = health.data?.llm;

  return (
    <PageHeader
      width="default"
      title="Settings"
      description="What the front desk runs on: the speech providers, how consent is collected, the phone line and privacy. These are read from the server. To change one, edit backend/.env and restart; keys are never shown here."
    >
      <div className="space-y-6">
        <section aria-labelledby="usage-title">
          <h2 id="usage-title" className="mb-3 font-display text-lg font-semibold text-text-primary">Usage, last 7 days</h2>
          <div className="grid gap-3 sm:grid-cols-3">
            <UsageTiles />
          </div>
        </section>
        <Section id="providers" title="Speech providers" intro="Prisma (Gnani) hears the caller; Timbre (Gnani) speaks and clones consented voices; a language model decides what to say.">
          {engines.isLoading ? (
            <Skeleton className="my-3 h-16 w-full" />
          ) : (
            <>
              <Row
                label="Gnani API key"
                note={!gnaniOn && gnaniKnown ? timbre?.unavailable_reason ?? undefined : undefined}
              >
                {gnaniKnown ? (
                  gnaniOn ? <Status tone="success">Set</Status> : <Status tone="warning">Not set</Status>
                ) : (
                  <Status tone="neutral">Unknown</Status>
                )}
              </Row>
              <Row label="Speech recognition" note="Gnani Prisma hears the caller, in Hindi, English and the other Indian languages.">
                {gnaniKnown ? (gnaniOn ? "Gnani Prisma" : "Needs a Gnani key") : "Not known yet"}
              </Row>
              <Row label="Voice (text to speech)" note="If Gnani cannot answer, a live caller hears a short held line and is offered a callback or a person.">
                {gnaniKnown ? (gnaniOn ? "Gnani Timbre" : "Needs a Gnani key") : "Not known yet"}
              </Row>
              <Row label="Voice cloning" note="Always behind the consent step below. Every clip gets a signed receipt and an AI label.">
                {gnaniKnown ? (gnaniOn ? "Gnani Timbre voice clone" : "Needs a Gnani key") : "Not known yet"}
              </Row>
            </>
          )}
          <Row label="Language model">
            {health.isLoading ? (
              <Skeleton className="h-5 w-40" />
            ) : llm?.active ? (
              <span className="flex flex-wrap items-center gap-2">
                <Status tone="success">{llmLabel(llm.active)}</Status>
              </span>
            ) : (
              <Status tone="warning">None set up</Status>
            )}
          </Row>
        </Section>

        <SpeechModelsCard />

        <Section id="consent" title="Consent" intro="Nobody's voice is cloned without their permission. This is how that permission is collected.">
          {consent.isLoading ? (
            <Skeleton className="my-3 h-16 w-full" />
          ) : consent.data ? (
            <>
              <Row label="Consent mode" note={<>Set by <code className={code}>CONSENT_MODE</code>.</>}>
                {consent.data.consent_mode === "strict" ? "Strict: the speaker reads a fresh sentence aloud" : "One tick: the speaker agrees with a checkbox"}
              </Row>
              {consent.data.statement && consent.data.consent_mode === "tick" && <Row label="Wording">{consent.data.statement}</Row>}
              {consent.data.retention_days != null && <Row label="Kept for">{consent.data.retention_days} days, or until consent is withdrawn and the voice deleted</Row>}
              {consent.data.contact && <Row label="Contact for questions">{consent.data.contact}</Row>}
            </>
          ) : (
            <Row label="Consent mode"><Status tone="neutral">Could not load</Status></Row>
          )}
        </Section>

        <HearingSettings />

        <Section id="phone" title="Phone provider" intro="The line callers ring, and the provider that carries it. Numbers are masked.">
          {phone.isLoading ? (
            <Skeleton className="my-3 h-16 w-full" />
          ) : phone.data ? (
            <>
              <Row label="Vobiz" note="Vobiz carries the calls: the number callers ring and the calls the front desk places.">
                <span className="flex flex-wrap items-center gap-2">
                  {phone.data.configured ? <Status tone="success">Configured</Status> : <Status tone="warning">Not configured</Status>}
                  {phone.data.configured && phone.data.provider_verified === false && <Badge tone="warning">Not yet tried on a real call</Badge>}
                </span>
              </Row>
              {!phone.data.configured && phone.data.missing.length > 0 && (
                <Row label="Missing settings">
                  <span className="flex flex-wrap gap-1.5">
                    {phone.data.missing.map((m) => <code key={m} className={code}>{m}</code>)}
                  </span>
                </Row>
              )}
              <Row label="Caller ID">{phone.data.caller_id_masked ?? "Not set"}</Row>
              <Row label="Public address" note="Incoming calls and web call links need a public https address (the tunnel).">
                {phone.data.inbound_ready ? <Status tone="success">Ready</Status> : <Status tone="warning">PUBLIC_BASE_URL not set</Status>}
              </Row>
              <Row label="Numbers it may call">
                {phone.data.allowed_numbers_set ? phone.data.allowed_numbers.map((n) => n.masked).join(", ") : "None yet"}
              </Row>
              <Row label="Longest call">{Math.round(phone.data.max_call_seconds / 60)} minutes</Row>
            </>
          ) : (
            <Row label="Phone line"><Status tone="neutral">Could not load</Status></Row>
          )}
        </Section>

        <WhatsAppSettings />

        <PhoneDeskSettings />

        <Section id="privacy" title="Privacy" intro="Where the data lives and what the server promises about it.">
          <Row label="Storage">
            {health.data ? (health.data.supabase?.configured ? "Cloud (Supabase) when chosen, otherwise this computer" : "This computer only (no cloud account is connected)") : <Skeleton className="h-5 w-48" />}
          </Row>
          <Row label="Signed statement" note="An Ed25519 signature over how this server is configured, so the claim can be checked.">
            {attestation.isLoading ? (
              <Skeleton className="h-5 w-48" />
            ) : attestation.data?.statement?.claim ? (
              <span className="flex flex-wrap items-center gap-2">
                <Status tone={attestation.data.statement.claim === "configured_to_keep_data_on_this_machine" ? "success" : "neutral"}>
                  {attestation.data.statement.claim === "configured_to_keep_data_on_this_machine" ? "Configured to keep data on this computer" : String(attestation.data.statement.claim).replace(/_/g, " ")}
                </Status>
                {attestation.data.key_id && <span className="text-xs text-text-secondary">key {attestation.data.key_id}</span>}
              </span>
            ) : (
              <Status tone="neutral">Not available</Status>
            )}
          </Row>
          <Row label="AI disclosure">The front desk says it is an AI at the start of each call, unless you turn that off on the Front desk page.</Row>
          <Row label="Callers it remembers" note="Forget anyone on the Callers page.">A name, language and last booking, for a limited time</Row>
        </Section>
      </div>
    </PageHeader>
  );
}

export default function SettingsPage() {
  return (
    <ErrorBoundary>
      <SettingsContent />
    </ErrorBoundary>
  );
}
