import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import { getPhoneNumbers } from "@/lib/api/client";
import { PhonePanel } from "@/components/settings/phone-panel";

// The dashboard layout already gates on business + plan; this page only
// handles its own data and treats 401/402 as a backstop.
export const dynamic = "force-dynamic";

export default async function PhonePage() {
  const { getToken } = await auth();
  const token = await getToken();
  if (!token) {
    redirect("/sign-in");
  }

  const result = await getPhoneNumbers(token);
  if (result.kind === "unauthorized") redirect("/sign-in");
  if (result.kind === "subscription_required") redirect("/billing/plan");

  return <PhonePanel result={result} />;
}
