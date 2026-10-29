import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <div role="status" aria-busy="true" className="flex w-full max-w-[1280px] flex-col gap-6">
      <span className="sr-only">Loading your integrations</span>
      <div className="flex flex-col gap-2">
        <Skeleton className="h-10 w-56" />
        <Skeleton className="h-5 w-full max-w-md" />
      </div>
      <div className="flex flex-wrap gap-2">
        <Skeleton className="h-9 w-28 rounded-md" />
        <Skeleton className="h-9 w-20 rounded-md" />
        <Skeleton className="h-9 w-16 rounded-md" />
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Skeleton className="h-44 rounded-md" />
        <Skeleton className="h-44 rounded-md" />
        <Skeleton className="h-44 rounded-md" />
        <Skeleton className="h-44 rounded-md" />
      </div>
      <Skeleton className="h-28 rounded-md" />
    </div>
  );
}
