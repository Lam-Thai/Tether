import type { ReactNode } from "react";

import type {
  BusinessResult,
  DashboardSummaryResult,
  IntegrationStatusResult,
  InteractionListResult,
} from "@/lib/api/client";
import { BusinessOverview } from "@/components/dashboard/business-overview";
import { IntegrationSection } from "@/components/dashboard/integration-status";
import { RecentInteractions } from "@/components/dashboard/recent-interactions";
import { SetupGuide, shouldShowSetupGuide } from "@/components/dashboard/setup-guide";
import { SummaryTiles } from "@/components/dashboard/summary-tiles";

/**
 * Presentational view of `/dashboard`. Every block takes its own fetch result,
 * so one failed request shows a Callout in that block and nothing else.
 */
export function DashboardHome({
  business,
  summary,
  integrations,
  recent,
  timeZone,
  banner,
}: {
  business: BusinessResult;
  summary: DashboardSummaryResult;
  integrations: IntegrationStatusResult;
  recent: InteractionListResult;
  /** The business timezone, for times in the integration blocks. */
  timeZone: string;
  /** Plan status, shown above everything else. */
  banner?: ReactNode;
}) {
  return (
    <div className="flex w-full max-w-[1280px] flex-col gap-8">
      {banner}
      <BusinessOverview result={business} />
      <SummaryTiles result={summary} />
      {shouldShowSetupGuide(integrations, recent) && <SetupGuide readiness={integrations.data.readiness} />}
      <section aria-labelledby="home-recent" className="flex flex-col gap-4">
        <h2 id="home-recent" className="text-h2 font-bold tracking-tight uppercase">
          Recent conversations
        </h2>
        <RecentInteractions result={recent} />
      </section>
      <section aria-labelledby="home-integrations" className="flex flex-col gap-4">
        <h2 id="home-integrations" className="text-h2 font-bold tracking-tight uppercase">
          Integrations
        </h2>
        <IntegrationSection result={integrations} timeZone={timeZone} />
      </section>
    </div>
  );
}
