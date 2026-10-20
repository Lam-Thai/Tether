import { auth } from "@clerk/nextjs/server";

import type { BusinessResult } from "@/lib/api/client";
import { getCurrentMe } from "@/lib/dashboard/current-me";
import { BillingStatus } from "@/components/billing/billing-status";
import { BusinessOverview } from "@/components/dashboard/business-overview";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const { getToken } = await auth();
  const token = await getToken();

  // Same request as the layout's: `getCurrentMe` (React `cache()`) dedupes it.
  const me = token ? await getCurrentMe(token) : ({ kind: "unauthorized" } as const);

  const business: BusinessResult =
    me.kind === "ok"
      ? me.data.business
        ? { kind: "ok", data: me.data.business }
        : { kind: "not_found" }
      : me;
  const subscription = me.kind === "ok" ? me.data.subscription : null;

  return (
    <div className="flex w-full max-w-3xl flex-col gap-6">
      {subscription && me.kind === "ok" && me.data.business && (
        <BillingStatus subscription={subscription} timeZone={me.data.business.timezone} />
      )}
      <BusinessOverview result={business} />
    </div>
  );
}
