import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <div role="status" aria-busy="true" className="flex w-full max-w-3xl flex-col gap-6">
      <span className="sr-only">Loading your phone numbers</span>
      <div className="flex flex-col gap-2">
        <Skeleton className="h-4 w-28" />
        <Skeleton className="h-10 w-40" />
        <Skeleton className="h-5 w-full max-w-md" />
      </div>
      <Skeleton className="h-40 rounded-md" />
      <Skeleton className="h-40 rounded-md" />
    </div>
  );
}
