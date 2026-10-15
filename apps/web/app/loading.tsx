// Next.js App Router convention: automatically used as the Suspense
// fallback for this route segment while `page.tsx`'s async Server
// Component awaits `getHealth()`. Shape mirrors the healthy-state layout
// in `components/status-view.tsx` so there's no layout shift once data
// resolves.
export default function Loading() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-8">
      <div className="mx-auto flex w-full max-w-xl flex-col gap-6">
        <div className="flex flex-col gap-2">
          <span className="font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase">
            Tether status
          </span>
          <div className="h-8 w-64 animate-pulse rounded bg-border motion-reduce:animate-none" />
          <div className="h-4 w-80 animate-pulse rounded bg-border motion-reduce:animate-none" />
        </div>

        <div className="grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-3">
          {["API", "Database", "Version"].map((label) => (
            <div key={label} className="flex flex-col gap-2 bg-card p-5">
              <span className="font-mono text-xs tracking-[0.15em] text-muted-foreground uppercase">
                {label}
              </span>
              <div className="h-5 w-20 animate-pulse rounded bg-border motion-reduce:animate-none" />
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
