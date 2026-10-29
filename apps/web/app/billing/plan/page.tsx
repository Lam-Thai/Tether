import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import { getBillingPlan, getMe } from "@/lib/api/client";
import { destinationFor } from "@/lib/billing/destination";
import { BillingShell } from "@/components/billing/billing-shell";
import { PlanChooser } from "@/components/billing/plan-chooser";
import { AccountDeletionPanel } from "@/components/settings/account-deletion-panel";
import { Callout } from "@/components/ui/callout";

export const metadata: Metadata = { title: "Choose a plan" };

// Decides on every request whether the owner still needs a plan.
export const dynamic = "force-dynamic";

export default async function PlanPage({
  searchParams,
}: {
  searchParams: Promise<{ canceled?: string }>;
}) {
  const { getToken } = await auth();
  const token = await getToken();
  if (!token) {
    redirect("/sign-in");
  }

  const me = await getMe(token);
  if (me.kind === "unauthorized") {
    redirect("/sign-in");
  }
  if (me.kind === "ok") {
    const destination = destinationFor(me.data);
    if (destination !== "/billing/plan") {
      redirect(destination);
    }
  }

  const [plan, { canceled }] = await Promise.all([getBillingPlan(token), searchParams]);

  return (
    <BillingShell>
      <div className="flex w-full flex-col items-center gap-12">
        {plan.kind === "ok" ? (
          <PlanChooser plan={plan.data} canceled={canceled === "1"} />
        ) : (
          <div className="w-full max-w-xl">
            <Callout tone="danger" title="Can't load plans" headingLevel={1}>
              {plan.kind === "unreachable"
                ? plan.reason
                : "Your session or business couldn't be confirmed. Please sign in again."}
            </Callout>
          </div>
        )}
        <AccountDeletionPanel variant="disclosure" />
      </div>
    </BillingShell>
  );
}
