import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import { BillingShell } from "@/components/billing/billing-shell";
import { CheckoutReturn } from "@/components/billing/checkout-return";

export const metadata: Metadata = { title: "Confirming your plan" };

export const dynamic = "force-dynamic";

export default async function CheckoutReturnPage({
  searchParams,
}: {
  searchParams: Promise<{ session_id?: string | string[] }>;
}) {
  const { userId } = await auth();
  if (!userId) {
    redirect("/sign-in");
  }
  const { session_id: raw } = await searchParams;
  const sessionId = Array.isArray(raw) ? raw[0] : raw;

  return (
    <BillingShell>
      <CheckoutReturn sessionId={sessionId ?? null} />
    </BillingShell>
  );
}
