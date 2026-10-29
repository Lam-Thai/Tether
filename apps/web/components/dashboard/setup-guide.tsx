import Link from "next/link";
import { ListChecks } from "lucide-react";

import type { AgentReadiness, InteractionListResult, IntegrationStatusResult } from "@/lib/api/client";
import { buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ReadinessList } from "./readiness-list";

/**
 * The new-owner guide (PRD 6.1): shown only when both fetches succeeded, the
 * business has no conversations and the receptionist is not active. If either
 * result is missing or failed, there is no way to know, so nothing renders.
 */
export function shouldShowSetupGuide(
  integrations: IntegrationStatusResult,
  recent: InteractionListResult,
): integrations is Extract<IntegrationStatusResult, { kind: "ok" }> {
  return (
    integrations.kind === "ok" &&
    recent.kind === "ok" &&
    recent.data.items.length === 0 &&
    !integrations.data.agent.active
  );
}

export function SetupGuide({ readiness }: { readiness: AgentReadiness }) {
  const remaining = readiness.items.filter((i) => i.required && !i.ok).length;
  return (
    <Card className="gap-4 p-6">
      <div className="flex items-start gap-3">
        <ListChecks aria-hidden className="mt-1 size-6 shrink-0" />
        <div className="flex min-w-0 flex-col gap-1">
          <h2 className="text-h2 font-bold tracking-tight uppercase">Get set up</h2>
          <p className="max-w-[72ch] text-base text-muted-foreground">
            {readiness.ready
              ? "Everything is in place. Activate your receptionist to start answering callers."
              : `${remaining} ${remaining === 1 ? "step" : "steps"} left before your receptionist can answer callers.`}
          </p>
        </div>
      </div>
      <ReadinessList items={readiness.items} label="Setup steps" />
      {readiness.ready && (
        <Link href="/dashboard/agent#activation" className={buttonVariants({ size: "sm" }) + " w-fit"}>
          Go to activation
        </Link>
      )}
    </Card>
  );
}
