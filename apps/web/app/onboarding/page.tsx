import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import { getMe } from "@/lib/api/client";
import { OnboardingForm } from "@/components/onboarding/onboarding-form";

// Always re-checks `GET /me` — this page's whole job is deciding whether
// the signed-in owner still needs to create a business, which must never
// be served from a cache.
export const dynamic = "force-dynamic";

export default async function OnboardingPage() {
  const { getToken } = await auth();
  const token = await getToken();

  if (token) {
    const me = await getMe(token);
    if (me.kind === "ok" && me.data.business) {
      redirect("/dashboard");
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-8">
      <OnboardingForm />
    </main>
  );
}
