import Link from "next/link";

import { getHealth } from "@/lib/api/client";
import { StatusView } from "@/components/status-view";

// This page's data (via getHealth -> fetch(..., { cache: "no-store" })) is
// a liveness check and must never be served from a static/prerendered
// cache. `force-dynamic` makes that explicit instead of relying only on
// Next's cache-based inference.
export const dynamic = "force-dynamic";

export default async function HomePage() {
  const result = await getHealth();

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-background p-8">
      <StatusView result={result} />
      <div className="flex items-center gap-4 text-sm">
        <Link href="/sign-in" className="text-foreground underline-offset-4 hover:underline">
          Sign in
        </Link>
        <Link href="/sign-up" className="text-foreground underline-offset-4 hover:underline">
          Sign up
        </Link>
      </div>
    </main>
  );
}
