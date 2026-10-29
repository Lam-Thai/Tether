import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import { destinationFor } from "@/lib/billing/destination";
import { getCurrentMe } from "@/lib/dashboard/current-me";
import { DashboardNav } from "@/components/dashboard/dashboard-nav";
import { SidebarFooter } from "@/components/dashboard/sidebar-footer";
import { Callout } from "@/components/ui/callout";
import { Wordmark } from "@/components/ui/construct";
import { Eyebrow } from "@/components/ui/eyebrow";

// Decides on every request whether the owner may be here at all (business
// + trialing/active plan), so it must never be served from a cache.
export const dynamic = "force-dynamic";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { getToken } = await auth();
  const token = await getToken();

  // Middleware already protects `/dashboard(.*)`, but never assume that.
  if (!token) {
    redirect("/sign-in");
  }

  const me = await getCurrentMe(token);

  // A token that passed middleware but the API rejects (expired between the
  // two checks, revoked session) means "not actually signed in".
  if (me.kind === "unauthorized") {
    redirect("/sign-in");
  }
  if (me.kind === "ok") {
    const destination = destinationFor(me.data);
    if (destination !== "/dashboard") {
      redirect(destination);
    }
  }

  const businessName = me.kind === "ok" && me.data.business ? me.data.business.name : "Tether";

  return (
    <div className="flex min-h-[100dvh] flex-col bg-background md:flex-row">
      <aside className="relative isolate flex flex-col gap-6 overflow-hidden bg-surface-dark p-6 text-surface-dark-foreground md:w-64 md:shrink-0 md:justify-between">
        <div
          aria-hidden
          className="absolute -top-6 -right-10 -z-10 h-24 w-28 -rotate-12 bg-brand-red [clip-path:polygon(25%_0,100%_0,100%_100%,0_100%)]"
        />
        <div className="flex flex-col gap-8">
          <Wordmark inverse />
          <div className="flex flex-col gap-1">
            <Eyebrow className="text-surface-dark-muted">Workspace</Eyebrow>
            <p className="truncate text-lg font-semibold">{businessName}</p>
          </div>
          <DashboardNav />
        </div>
        <SidebarFooter />
      </aside>
      <main className="animate-enter flex-1 p-6 md:p-10">
        {/* Without /me we can't confirm the plan, so no dashboard content. */}
        {me.kind === "unreachable" ? (
          <div className="max-w-3xl">
            <Callout tone="danger" title="Can't load your workspace" headingLevel={1}>
              {me.reason}
            </Callout>
          </div>
        ) : (
          children
        )}
      </main>
    </div>
  );
}
