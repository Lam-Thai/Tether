import type { Metadata } from "next";

import { CalendarCallback } from "@/components/settings/calendar-callback";

export const metadata: Metadata = { title: "Connecting your calendar" };

// Google puts one-time values in this URL, so it must never be cached.
export const dynamic = "force-dynamic";

function first(value: string | string[] | undefined): string | null {
  const single = Array.isArray(value) ? value[0] : value;
  return single ? single : null;
}

export default async function CalendarCallbackPage({
  searchParams,
}: {
  searchParams: Promise<{ code?: string | string[]; state?: string | string[]; error?: string | string[] }>;
}) {
  // The dashboard layout has already required a signed-in owner with a plan.
  const params = await searchParams;

  return (
    <CalendarCallback code={first(params.code)} state={first(params.state)} error={first(params.error)} />
  );
}
