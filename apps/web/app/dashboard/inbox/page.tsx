import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import { listInteractions } from "@/lib/api/client";
import { parseInboxQuery } from "@/lib/inbox/query";
import { InboxView } from "@/components/inbox/inbox-view";

export const metadata: Metadata = { title: "Inbox" };

// The dashboard layout already gates on business + plan; this page only
// handles its own data and treats 401/402 as a backstop.
export const dynamic = "force-dynamic";

export default async function InboxPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { getToken } = await auth();
  const token = await getToken();
  if (!token) {
    redirect("/sign-in");
  }

  const query = parseInboxQuery(await searchParams);
  const result = await listInteractions(token, query);
  if (result.kind === "unauthorized") redirect("/sign-in");
  if (result.kind === "subscription_required") redirect("/billing/plan");

  return <InboxView result={result} query={query} />;
}
