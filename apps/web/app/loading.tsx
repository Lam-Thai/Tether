import { Eyebrow } from "@/components/ui/eyebrow";
import { Skeleton } from "@/components/ui/skeleton";

// Next.js App Router convention: the Suspense fallback while `page.tsx`
// awaits `getHealth()`. Mirrors the healthy-state layout of
// `components/status-view.tsx` so nothing shifts once data resolves.
export default function Loading() {
  return (
    <div className="mx-auto flex min-h-[100dvh] w-full max-w-[1280px] flex-col px-6">
      <div className="h-16" />
      <main className="flex flex-1 items-start py-[clamp(3rem,8vw,6rem)]">
        <div role="status" aria-busy="true" className="flex w-full max-w-3xl flex-col gap-8">
          <span className="sr-only">Loading status</span>
          <div className="flex flex-col gap-3">
            <Eyebrow>Tether status</Eyebrow>
            <Skeleton className="h-10 w-full max-w-md" />
            <Skeleton className="h-5 w-full max-w-sm" />
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-[2fr_1fr_1fr]">
            {["API", "Database", "Version"].map((label) => (
              <div
                key={label}
                className="flex flex-col gap-3 rounded-md border border-border bg-card p-5 shadow-card"
              >
                <Eyebrow>{label}</Eyebrow>
                <Skeleton className="h-7 w-24" />
              </div>
            ))}
          </div>
        </div>
      </main>
    </div>
  );
}
