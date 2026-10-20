import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <div className="mx-auto flex min-h-[100dvh] w-full max-w-[1280px] flex-col px-6">
      <div className="h-16" />
      <main className="flex flex-1 justify-center py-[clamp(2rem,6vw,5rem)]">
        <div role="status" aria-busy="true" className="flex w-full max-w-4xl flex-col gap-8">
          <span className="sr-only">Loading plans</span>
          <div className="flex flex-col gap-3">
            <Skeleton className="h-10 w-full max-w-sm" />
            <Skeleton className="h-5 w-full max-w-md" />
          </div>
          <div className="grid grid-cols-1 gap-6 md:grid-cols-[3fr_2fr]">
            <Skeleton className="h-80 rounded-md" />
            <Skeleton className="h-80 rounded-md" />
          </div>
        </div>
      </main>
    </div>
  );
}
