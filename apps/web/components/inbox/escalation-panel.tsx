"use client";

import Link from "next/link";
import { createContext, useContext, useState, type ReactNode } from "react";
import { useAuth } from "@clerk/nextjs";
import { CircleCheck, Siren } from "lucide-react";

import { resolveEscalation, type EscalationRead } from "@/lib/api/client";
import { SESSION_EXPIRED } from "@/components/agent/section-shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { AttentionBadge, EscalationBadge } from "./badges";
import { escalationReasonLabel, formatBusinessTime, smsStatusLabel } from "./labels";

type EscalationState = {
  escalations: EscalationRead[];
  replace: (next: EscalationRead) => void;
};

const EscalationContext = createContext<EscalationState | null>(null);

/**
 * Holds the interaction's escalations after mount, so the header badge and the
 * panel stay in step once a Resolve is confirmed by the API.
 */
export function EscalationProvider({ initial, children }: { initial: EscalationRead[]; children: ReactNode }) {
  const [escalations, setEscalations] = useState(initial);
  const replace = (next: EscalationRead) =>
    setEscalations((current) => current.map((item) => (item.id === next.id ? next : item)));
  return <EscalationContext.Provider value={{ escalations, replace }}>{children}</EscalationContext.Provider>;
}

function useEscalations(): EscalationState {
  const state = useContext(EscalationContext);
  if (!state) throw new Error("Escalation components need an EscalationProvider.");
  return state;
}

/**
 * Header mark: "Needs attention" while any escalation is open; the legacy
 * "Needs a person" only when the flag is set and no escalation was recorded.
 */
export function EscalationHeaderBadge({ requiresHuman }: { requiresHuman: boolean }) {
  const { escalations } = useEscalations();
  const open = escalations.filter((item) => item.status === "open");
  if (open.length > 0) {
    const lead = open.find((item) => item.urgency === "high") ?? open[0]!;
    return <AttentionBadge reason={lead.reason} />;
  }
  if (requiresHuman && escalations.length === 0) return <EscalationBadge />;
  return null;
}

type ResolveState = { id: string; kind: "pending" } | { id: string; kind: "error"; message: ReactNode } | null;

export function EscalationPanel({ timezone }: { timezone: string }) {
  const { escalations, replace } = useEscalations();
  const { getToken } = useAuth();
  const [resolving, setResolving] = useState<ResolveState>(null);

  if (escalations.length === 0) return null;

  async function resolve(id: string) {
    setResolving({ id, kind: "pending" });
    const token = await getToken();
    if (!token) {
      setResolving({ id, kind: "error", message: SESSION_EXPIRED });
      return;
    }

    const result = await resolveEscalation(token, id);
    if (result.kind === "ok") {
      // Only now is it resolved: adopt what the API returned.
      replace(result.data);
      setResolving(null);
      return;
    }

    let message: ReactNode;
    switch (result.kind) {
      case "unauthorized":
        message = SESSION_EXPIRED;
        break;
      case "subscription_required":
        message = (
          <>
            Your plan is not active. <Link href="/billing/plan" className="font-medium underline">Choose a plan</Link>.
          </>
        );
        break;
      case "not_found":
        message = "This escalation no longer exists. Reload the page.";
        break;
      case "unreachable":
        message = result.reason;
        break;
    }
    setResolving({ id, kind: "error", message });
  }

  return (
    <section aria-labelledby="escalation-heading" className="flex flex-col gap-3">
      <h2 id="escalation-heading" className="text-h2 font-bold tracking-tight uppercase">
        Escalation
      </h2>
      <ul aria-label="Escalations" className="flex flex-col gap-3">
        {escalations.map((item) => {
          const pending = resolving?.id === item.id && resolving.kind === "pending";
          const failure = resolving?.id === item.id && resolving.kind === "error" ? resolving : null;
          return (
            <li
              key={item.id}
              className="flex flex-col gap-3 rounded-md border border-border bg-card p-4 shadow-card"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-base font-bold">{escalationReasonLabel(item.reason)}</span>
                {item.urgency === "high" && (
                  <Badge tone="danger">
                    <Siren aria-hidden />
                    Urgent
                  </Badge>
                )}
                <Badge tone={item.status === "open" ? "warning" : "neutral"}>
                  {item.status === "open" ? "Open" : "Resolved"}
                </Badge>
              </div>
              <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
                <dt className="text-muted-foreground">Text message</dt>
                <dd>
                  {smsStatusLabel(item.sms_status)}
                  {item.sms_error && (
                    <>
                      {" "}
                      <span className="font-mono text-muted-foreground">Error code: {item.sms_error}</span>
                    </>
                  )}
                </dd>
                <dt className="text-muted-foreground">Raised</dt>
                <dd>
                  <time dateTime={item.created_at}>{formatBusinessTime(item.created_at, timezone)}</time>
                </dd>
                {item.resolved_at && (
                  <>
                    <dt className="text-muted-foreground">Resolved</dt>
                    <dd>
                      <time dateTime={item.resolved_at}>{formatBusinessTime(item.resolved_at, timezone)}</time>
                    </dd>
                  </>
                )}
              </dl>
              {failure && (
                <div role="alert">
                  <Callout tone="danger" compact headingLevel={3} title="Couldn't resolve this">
                    {failure.message}
                  </Callout>
                </div>
              )}
              {item.status === "open" ? (
                <div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={pending}
                    aria-busy={pending || undefined}
                    onClick={() => resolve(item.id)}
                  >
                    {pending ? "Resolving…" : "Resolve"}
                    <span className="sr-only"> {escalationReasonLabel(item.reason)}</span>
                  </Button>
                </div>
              ) : (
                <p role="status" className="flex items-center gap-1.5 text-sm">
                  <CircleCheck aria-hidden className="size-4" />
                  Resolved
                </p>
              )}
            </li>
          );
        })}
      </ul>
      <p role="status" aria-live="polite" className="sr-only">
        {resolving?.kind === "pending" ? "Resolving escalation" : ""}
      </p>
    </section>
  );
}
