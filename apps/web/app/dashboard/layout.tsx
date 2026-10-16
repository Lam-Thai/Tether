import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import { getCurrentBusiness } from "@/lib/dashboard/current-business";
import { DashboardNav } from "@/components/dashboard/dashboard-nav";
import { SignOutButton } from "@/components/dashboard/sign-out-button";
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
    <div className="flex min-h-screen bg-background">
      <aside className="flex w-64 flex-col justify-between border-r border-border bg-card p-6">
        <div className="flex flex-col gap-8">
          <div className="flex flex-col gap-1">
            <Eyebrow>Workspace</Eyebrow>
            <p className="truncate text-sm font-semibold text-foreground">{businessName}</p>
          </div>
          <DashboardNav />
        </div>
        <SignOutButton />
      </aside>
      <main className="flex-1 p-8">{children}</main>
    </div>
  );
}
