import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <div role="status" aria-busy="true" className="flex w-full max-w-4xl flex-col gap-6">
      <span className="sr-only">Loading the conversation</span>
      <Skeleton className="h-9 w-36 rounded-md" />
      <div className="flex flex-col gap-2">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-10 w-56" />
        <Skeleton className="h-7 w-24" />
      </div>
      <Skeleton className="h-32 rounded-md" />
      <Skeleton className="h-10 w-full max-w-md" />
      <div className="flex flex-col gap-3">
        <Skeleton className="h-16 w-3/4 rounded-md" />
        <Skeleton className="ml-auto h-16 w-2/3 rounded-md" />
        <Skeleton className="h-16 w-3/4 rounded-md" />
      </div>
    </div>
  );
}
