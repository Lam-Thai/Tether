import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import { getAgent, getTestTranscript, listFaqs } from "@/lib/api/client";
import type { ChatLoad } from "@/components/agent/test-chat";
import { AgentWorkspace } from "@/components/agent/agent-workspace";

// The dashboard layout already redirects owners without a business or an
// entitled plan (via `destinationFor`), so this page only handles its own data.
export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default async function AgentPage({
  searchParams,
}: {
  searchParams: Promise<{ chat?: string | string[] }>;
}) {
  const { getToken } = await auth();
  const token = await getToken();
  if (!token) {
    redirect("/sign-in");
  }

  const { chat } = await searchParams;
  const chatId = typeof chat === "string" && UUID.test(chat) ? chat : null;

  const [agent, transcript, faqs] = await Promise.all([
    getAgent(token),
    chatId ? getTestTranscript(token, chatId) : Promise.resolve(null),
    listFaqs(token),
  ]);

  if (agent.kind === "unauthorized") redirect("/sign-in");
  if (agent.kind === "subscription_required") redirect("/billing/plan");
  if (faqs.kind === "unauthorized") redirect("/sign-in");
  if (faqs.kind === "subscription_required") redirect("/billing/plan");

  let chatLoad: ChatLoad = { kind: "none" };
  if (chat !== undefined && !chatId) {
    // `?chat=` was present but is not a UUID: same as an unknown chat.
    chatLoad = { kind: "unavailable", reason: "not_found" };
  } else if (transcript) {
    switch (transcript.kind) {
      case "ok":
        chatLoad = { kind: "restored", transcript: transcript.data };
        break;
      case "not_found":
        chatLoad = { kind: "unavailable", reason: "not_found" };
        break;
      case "unreachable":
        chatLoad = { kind: "unavailable", reason: "unreachable" };
        break;
      case "unauthorized":
        redirect("/sign-in");
        break;
      case "subscription_required":
        redirect("/billing/plan");
        break;
    }
  }

  return <AgentWorkspace agent={agent} chat={chatLoad} faqs={faqs} />;
}
