import type { SubscriptionRead } from "@/lib/api/client";
import { daysUntil } from "@/lib/billing/format";
import { Badge } from "@/components/ui/badge";
import { Callout } from "@/components/ui/callout";
import { Eyebrow } from "@/components/ui/eyebrow";
import { ManageBillingButton } from "@/components/billing/manage-billing-button";

/** In the business's own timezone, not wherever the server happens to run. */
function formatDate(iso: string, timeZone: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone,
  });
}

/** The owner's plan at a glance, with the way to change it. */
export function BillingStatus({
  subscription,
  timeZone,
  now = new Date(),
}: {
  subscription: SubscriptionRead;
  timeZone: string;
  now?: Date;
}) {
  if (subscription.status === "past_due") {
    return (
      <Callout tone="danger" title="Payment failed" action={<ManageBillingButton variant="default" />}>
        Stripe couldn&apos;t charge your card. Update it in billing to keep Tether answering.
      </Callout>
    );
  }

  const trialDays =
    subscription.status === "trialing" && subscription.trial_ends_at
      ? daysUntil(subscription.trial_ends_at, now)
      : null;

  return (
    <section
      aria-label="Plan"
      className="flex flex-col gap-4 rounded-md border border-border bg-card p-5 shadow-card md:flex-row md:items-center md:justify-between"
    >
      <div className="flex flex-col gap-2">
        <Eyebrow>Plan</Eyebrow>
        <div className="flex flex-wrap items-center gap-3">
          {trialDays !== null ? (
            <Badge tone="warning">
              Free trial: {trialDays} {trialDays === 1 ? "day" : "days"} left
            </Badge>
          ) : (
            <Badge tone="success">Active</Badge>
          )}
          {subscription.cancel_at_period_end && subscription.current_period_end && (
            <span className="text-base text-muted-foreground">
              Ends on {formatDate(subscription.current_period_end, timeZone)}
            </span>
          )}
        </div>
      </div>
      <ManageBillingButton />
    </section>
  );
}
