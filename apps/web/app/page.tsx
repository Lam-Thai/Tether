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
    <main className="flex min-h-screen items-center justify-center bg-background p-8">
      <StatusView result={result} />
    </main>
  );
}
