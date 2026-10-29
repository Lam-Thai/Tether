import Link from "next/link";

import type { IntegrationStatusResult } from "@/lib/api/client";
import { IntegrationSection } from "@/components/dashboard/integration-status";
import { ReadinessList } from "@/components/dashboard/readiness-list";
import { SettingsNav } from "@/components/settings/settings-nav";
import { buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

/**
 * Presentational view of `/dashboard/settings/integrations` (PRD 12.4): calendar,
 * phone, activation, recent errors and usage limits from one
 * `GET /integrations/status`. Loading lives in the route's `loading.tsx`.
 */
export function IntegrationsPanel({
  result,
  timeZone,
}: {
  result: IntegrationStatusResult;
  timeZone: string;
}) {
  const incomplete = result.kind === "ok" && (!result.data.agent.active || !result.data.agent.ready);

  return (
    <div className="flex w-full max-w-[1280px] flex-col gap-6">
      <SettingsNav current="integrations" />
      <div className="flex flex-col gap-2">
        <h1 className="text-h1 font-bold tracking-tight uppercase">Integrations</h1>
        <p className="max-w-[72ch] text-base text-muted-foreground">
          What your receptionist is connected to, whether it is switched on, and anything that
          recently went wrong.
        </p>
      </div>
      <IntegrationSection result={result} timeZone={timeZone} detailed headingLevel="h2" />
      {result.kind === "ok" && incomplete && (
        <Card className="gap-4 p-6">
          <h2 className="text-lg font-semibold">Activation checklist</h2>
          <ReadinessList items={result.data.readiness.items} label="Activation checklist" />
          <Link
            href="/dashboard/agent#activation"
            className={buttonVariants({ variant: result.data.agent.ready ? "default" : "outline", size: "sm" }) + " w-fit"}
          >
            {result.data.agent.ready ? "Go to activation" : "Open the Agent Builder"}
          </Link>
        </Card>
      )}
    </div>
  );
}
