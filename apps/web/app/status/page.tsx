import Link from "next/link";

import { getHealth } from "@/lib/api/client";
import { StatusView } from "@/components/status-view";
import { buttonVariants } from "@/components/ui/button";
import { Wordmark } from "@/components/ui/construct";

// This page's data (via getHealth -> fetch(..., { cache: "no-store" })) is
// a liveness check and must never be served from a static/prerendered
// cache. `force-dynamic` makes that explicit instead of relying only on
// Next's cache-based inference.
export const dynamic = "force-dynamic";

export const metadata = { title: "System status" };

export default async function StatusPage() {
  const result = await getHealth();

  return (
    <div className="mx-auto flex min-h-[100dvh] w-full max-w-[1280px] flex-col px-6">
      <header className="flex h-16 items-center justify-between gap-4">
        <Wordmark />
        <nav aria-label="Account" className="flex items-center gap-4">
          <Link
            href="/sign-in"
            className="text-base font-medium text-foreground underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            Sign in
          </Link>
          <Link href="/sign-up" className={buttonVariants({ size: "sm" })}>
            Start free trial
          </Link>
        </nav>
      </header>
      <main className="animate-enter flex flex-1 items-start py-[clamp(3rem,8vw,6rem)]">
        <StatusView result={result} />
      </main>
    </div>
  );
}
