import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import {
  getDashboardSummary,
  getIntegrationStatus,
  listInteractions,
  type BusinessResult,
} from "@/lib/api/client";
import { getCurrentMe } from "@/lib/dashboard/current-me";
import { BillingStatus } from "@/components/billing/billing-status";
import { DashboardHome } from "@/components/dashboard/dashboard-home";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const { getToken } = await auth();
  const token = await getToken();
  if (!token) {
    redirect("/sign-in");
  }

  // `getCurrentMe` is the layout's request too (React `cache()` dedupes it).
  // The three data blocks load in parallel and fail independently.
  const [me, summary, integrations, recent] = await Promise.all([
    getCurrentMe(token),
    getDashboardSummary(token),
    getIntegrationStatus(token),
    listInteractions(token, { limit: 5 }),
  ]);

  // The layout gates on business + plan; a 401/402 here is a backstop.
  if (
    me.kind === "unauthorized" ||
    summary.kind === "unauthorized" ||
    integrations.kind === "unauthorized" ||
    recent.kind === "unauthorized"
  ) {
    redirect("/sign-in");
  }
  if (
    summary.kind === "subscription_required" ||
    integrations.kind === "subscription_required" ||
    recent.kind === "subscription_required"
  ) {
    redirect("/billing/plan");
  }

  const business: BusinessResult =
    me.kind === "ok"
      ? me.data.business
        ? { kind: "ok", data: me.data.business }
        : { kind: "not_found" }
      : me;
  const subscription = me.kind === "ok" ? me.data.subscription : null;
  const timeZone = me.kind === "ok" && me.data.business ? me.data.business.timezone : "UTC";

  return (
    <DashboardHome
      business={business}
      summary={summary}
      integrations={integrations}
      recent={recent}
      timeZone={timeZone}
      banner={
        subscription && me.kind === "ok" && me.data.business ? (
          <BillingStatus subscription={subscription} timeZone={me.data.business.timezone} />
        ) : null
      }
    />
  );
}
