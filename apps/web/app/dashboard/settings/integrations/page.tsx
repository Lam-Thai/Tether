import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import { getIntegrationStatus } from "@/lib/api/client";
import { getCurrentMe } from "@/lib/dashboard/current-me";
import { IntegrationsPanel } from "@/components/settings/integrations-panel";

export const metadata: Metadata = { title: "Integrations" };

// The dashboard layout already gates on business + plan; this page only
// handles its own data and treats 401/402 as a backstop.
export const dynamic = "force-dynamic";

export default async function IntegrationsPage() {
  const { getToken } = await auth();
  const token = await getToken();
  if (!token) {
    redirect("/sign-in");
  }

  const [result, me] = await Promise.all([getIntegrationStatus(token), getCurrentMe(token)]);
  if (result.kind === "unauthorized") redirect("/sign-in");
  if (result.kind === "subscription_required") redirect("/billing/plan");

  const timeZone = me.kind === "ok" && me.data.business ? me.data.business.timezone : "UTC";
  return <IntegrationsPanel result={result} timeZone={timeZone} />;
}
