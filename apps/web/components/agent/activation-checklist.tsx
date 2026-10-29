"use client";

import { useRef, useState, type ReactNode } from "react";
import { useAuth } from "@clerk/nextjs";
import { CircleCheck, CirclePause, LoaderCircle, Power, PowerOff, RefreshCw } from "lucide-react";

import {
  activateAgent,
  deactivateAgent,
  getAgentReadiness,
  type AgentReadiness,
  type AgentReadinessResult,
} from "@/lib/api/client";
import { Dialog } from "@/components/agent/faq-dialog";
import { PLAN_REQUIRED, SESSION_EXPIRED } from "@/components/agent/section-shared";
import { ReadinessList } from "@/components/dashboard/readiness-list";
import { SectionFailure } from "@/components/dashboard/integration-status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FormError } from "@/components/ui/form-error";

type Busy = "activate" | "deactivate" | "refresh" | null;

/**
 * Agent Builder "Activation": the readiness checklist and the switch that makes
 * the receptionist answer callers. `active` and the items only ever come from an
 * API response; a click never changes what is shown on its own.
 */
export function ActivationChecklist({ result }: { result: AgentReadinessResult }) {
  if (result.kind !== "ok") {
    return (
      <SectionFailure
        result={result}
        title="Can't load activation status"
        planNote="Choose a plan to activate your receptionist."
      />
    );
  }
  return <ActivationCard initial={result.data} />;
}

function ActivationCard({ initial }: { initial: AgentReadiness }) {
  const { getToken } = useAuth();
  const [readiness, setReadiness] = useState<AgentReadiness>(initial);
  const [busy, setBusy] = useState<Busy>(null);
  // A ref as well as state, so two quick clicks cannot both start a request.
  const inFlight = useRef(false);
  const [error, setError] = useState<ReactNode>(null);
  const [confirming, setConfirming] = useState(false);

  async function run(kind: Exclude<Busy, null>, action: (token: string) => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(kind);
    setError(null);
    try {
      const token = await getToken();
      if (!token) {
        setError(SESSION_EXPIRED);
        return;
      }
      await action(token);
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  }

  /** Re-reads the checklist. A failed read keeps the old items and says so. */
  async function reload(token: string, failMessage: string): Promise<void> {
    const fresh = await getAgentReadiness(token);
    if (fresh.kind === "ok") setReadiness(fresh.data);
    else if (fresh.kind === "unauthorized") setError(SESSION_EXPIRED);
    else if (fresh.kind === "subscription_required") setError(PLAN_REQUIRED);
    else setError(failMessage);
  }

  const activate = () =>
    run("activate", async (token) => {
      const result = await activateAgent(token);
      switch (result.kind) {
        case "ok":
          setReadiness(result.data);
          return;
        case "not_ready":
          // The checklist was out of date: show the current one, still inactive.
          setError(result.message);
          await reload(token, `${result.message} Reload the page to see which steps are open.`);
          return;
        case "unauthorized":
          setError(SESSION_EXPIRED);
          return;
        case "subscription_required":
          setError(PLAN_REQUIRED);
          return;
        case "unreachable":
          setError("Couldn't confirm activation. Check the status below, or reload the page.");
          return;
      }
    });

  const deactivate = () =>
    run("deactivate", async (token) => {
      const result = await deactivateAgent(token);
      switch (result.kind) {
        case "ok":
          setReadiness(result.data);
          setConfirming(false);
          return;
        case "unauthorized":
          setError(SESSION_EXPIRED);
          break;
        case "subscription_required":
          setError(PLAN_REQUIRED);
          break;
        case "unreachable":
          setError("Couldn't confirm deactivation. Your receptionist may still be answering. Reload the page to check.");
          break;
      }
      // Keep the confirm dialog (and its error) in view.
      setConfirming(true);
    });

  const refresh = () =>
    run("refresh", (token) => reload(token, "Couldn't refresh the checklist. Try again in a moment."));

  const { active, ready, items } = readiness;
  const pending = busy !== null;
  const incomplete = items.filter((i) => i.required && !i.ok).length;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <CardTitle as="h2">Activation</CardTitle>
          {active ? (
            <Badge tone="success">
              <CircleCheck aria-hidden />
              Active
            </Badge>
          ) : (
            <Badge tone="warning">
              <CirclePause aria-hidden />
              Inactive
            </Badge>
          )}
        </div>
        <CardDescription>
          {active
            ? "Your receptionist is answering callers."
            : "Until you activate, callers hear that you're not available. The test chat keeps working."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <ReadinessList items={items} label="Activation checklist" />

        {active && !ready && (
          <p className="text-sm text-foreground">
            {incomplete === 1 ? "1 setup step is" : `${incomplete} setup steps are`} no longer complete. Fix{" "}
            {incomplete === 1 ? "it" : "them"} so callers can still be helped.
          </p>
        )}

        {error && !confirming && <FormError>{error}</FormError>}

        <div className="flex flex-wrap items-center gap-3">
          {active ? (
            <Button type="button" variant="destructive" onClick={() => setConfirming(true)} disabled={pending}>
              <PowerOff aria-hidden />
              Deactivate
            </Button>
          ) : (
            <Button type="button" onClick={activate} disabled={!ready || pending} aria-busy={busy === "activate" || undefined}>
              {busy === "activate" ? (
                <LoaderCircle aria-hidden className="animate-spin motion-reduce:animate-none" />
              ) : (
                <Power aria-hidden />
              )}
              {busy === "activate" ? "Activating…" : "Activate"}
            </Button>
          )}
          <Button type="button" variant="outline" onClick={refresh} disabled={pending} aria-busy={busy === "refresh" || undefined}>
            <RefreshCw aria-hidden className={busy === "refresh" ? "animate-spin motion-reduce:animate-none" : undefined} />
            Check again
          </Button>
        </div>
        {!active && !ready && (
          <p className="text-sm text-muted-foreground">
            Finish the steps marked above, then check again. Activate turns on when every step is done.
          </p>
        )}
      </CardContent>

      <Dialog open={confirming} onClose={() => setConfirming(false)} title="Deactivate your receptionist?">
        <div className="flex flex-col gap-5">
          {error && <FormError>{error}</FormError>}
          <p className="text-base text-muted-foreground">
            Callers will hear that you&apos;re not available, and your receptionist will stop booking and answering
            calls. Your settings stay as they are.
          </p>
          <div className="flex flex-wrap justify-end gap-3">
            <Button type="button" variant="outline" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button type="button" variant="destructive" onClick={deactivate} disabled={pending} aria-busy={busy === "deactivate" || undefined}>
              {busy === "deactivate" ? <LoaderCircle aria-hidden className="animate-spin motion-reduce:animate-none" /> : <PowerOff aria-hidden />}
              {busy === "deactivate" ? "Deactivating…" : "Deactivate"}
            </Button>
          </div>
        </div>
      </Dialog>
    </Card>
  );
}
