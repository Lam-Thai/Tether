import { auth } from "@clerk/nextjs/server";

import type { BusinessResult } from "@/lib/api/client";
import { getCurrentBusiness } from "@/lib/dashboard/current-business";
import { BusinessOverview } from "@/components/dashboard/business-overview";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const { getToken } = await auth();
  const token = await getToken();

  // Same token this request's layout already resolved — `getCurrentBusiness`
  // (React `cache()`) dedupes this into the layout's own `GET /business` call.
  const result: BusinessResult = token
    ? await getCurrentBusiness(token)
    : { kind: "unauthorized" };

  return (
    <div className="flex w-full max-w-3xl flex-col gap-6">
      <BusinessOverview result={result} />
    </div>
  );
}
