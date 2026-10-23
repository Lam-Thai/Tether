import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import { getCalendarStatus } from "@/lib/api/client";
import { CalendarPanel } from "@/components/settings/calendar-panel";

// The dashboard layout already gates on business + plan; this page only
// handles its own data and treats 401/402 as a backstop.
export const dynamic = "force-dynamic";

export default async function CalendarPage() {
  const { getToken } = await auth();
  const token = await getToken();
  if (!token) {
    redirect("/sign-in");
  }

  const status = await getCalendarStatus(token);
  if (status.kind === "unauthorized") redirect("/sign-in");
  if (status.kind === "subscription_required") redirect("/billing/plan");

  return <CalendarPanel status={status} />;
}
