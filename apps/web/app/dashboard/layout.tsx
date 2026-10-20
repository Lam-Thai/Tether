import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import { getCurrentBusiness } from "@/lib/dashboard/current-business";
import { DashboardNav } from "@/components/dashboard/dashboard-nav";
import { SignOutButton } from "@/components/dashboard/sign-out-button";
import { Wordmark } from "@/components/ui/construct";
import { Eyebrow } from "@/components/ui/eyebrow";

// This layout's whole job is deciding whether the signed-in owner has a
// business yet, which must never be served from a cache.
export const dynamic = "force-dynamic";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { getToken } = await auth();
  const token = await getToken();

  // Middleware already protects `/dashboard(.*)`, so this should only be
  // reachable with a session — but never assume that without checking.
  if (!token) {
    redirect("/sign-in");
  }

  const business = await getCurrentBusiness(token);

  if (business.kind === "not_found") {
    redirect("/onboarding");
  }

  // A token that passed middleware but the API itself rejects (expired
  // between the two checks, clock skew, revoked session) is the same
  // "not actually signed in" case as the missing-token branch above.
  if (business.kind === "unauthorized") {
    redirect("/sign-in");
  }

  const businessName = business.kind === "ok" ? business.data.name : "Tether";

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
        <SignOutButton />
      </aside>
      <main className="animate-enter flex-1 p-6 md:p-10">{children}</main>
    </div>
  );
}
