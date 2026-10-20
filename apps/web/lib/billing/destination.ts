import type { MeRead } from "@/lib/api/client";

export type AppDestination = "/onboarding" | "/billing/plan" | "/dashboard";

/**
 * Where a signed-in owner belongs right now. Every gated page asks this
 * instead of re-deriving the rules: no business -> onboarding, no
 * trialing/active/past_due plan -> plan selection, otherwise the dashboard.
 * The API enforces the same rule (402), this only decides the redirect.
 */
export function destinationFor(me: MeRead): AppDestination {
  if (me.business === null) {
    return "/onboarding";
  }
  if (!me.subscription?.entitled) {
    return "/billing/plan";
  }
  return "/dashboard";
}
