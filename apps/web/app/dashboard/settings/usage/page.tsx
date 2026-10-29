import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import { getUsage } from "@/lib/api/client";
import { SettingsNav } from "@/components/settings/settings-nav";
import { UsagePanel } from "@/components/settings/usage-panel";

// The dashboard layout already gates on business + plan; this page only
// handles its own data and treats 401/402 as a backstop.
export const dynamic = "force-dynamic";

export default async function UsagePage() {
  const { getToken } = await auth();
  const token = await getToken();
  if (!token) {
    redirect("/sign-in");
  }

  const usage = await getUsage(token);
  if (usage.kind === "unauthorized") redirect("/sign-in");
  if (usage.kind === "subscription_required") redirect("/billing/plan");

  return (
    <div className="flex w-full max-w-[1280px] flex-col gap-6">
      <SettingsNav current="usage" />
      <UsagePanel usage={usage} />
    </div>
  );
}
