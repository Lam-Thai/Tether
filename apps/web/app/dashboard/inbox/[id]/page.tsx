import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import { getInteraction, listInteractionMessages } from "@/lib/api/client";
import { inboxHref, parseInboxQuery } from "@/lib/inbox/query";
import { InteractionDetailView } from "@/components/inbox/detail-view";

export const metadata: Metadata = { title: "Conversation" };

// The dashboard layout already gates on business + plan; this page only
// handles its own data and treats 401/402 as a backstop.
export const dynamic = "force-dynamic";

export default async function InteractionPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { getToken } = await auth();
  const token = await getToken();
  if (!token) {
    redirect("/sign-in");
  }

  const { id } = await params;
  // The inbox query rides along on the link here, so Back returns to the same filters and page.
  const backHref = inboxHref(parseInboxQuery(await searchParams));

  const [detail, messages] = await Promise.all([
    getInteraction(token, id),
    listInteractionMessages(token, id, 0),
  ]);
  if (detail.kind === "unauthorized" || messages.kind === "unauthorized") redirect("/sign-in");
  if (detail.kind === "subscription_required" || messages.kind === "subscription_required") {
    redirect("/billing/plan");
  }

  return <InteractionDetailView detail={detail} messages={messages} backHref={backHref} />;
}
