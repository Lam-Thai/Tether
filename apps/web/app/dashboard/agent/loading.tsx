import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <div role="status" aria-busy="true" className="flex w-full max-w-[1280px] flex-col gap-6">
      <span className="sr-only">Loading your agent</span>
      <Skeleton className="h-10 w-40" />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Skeleton className="h-[36rem] rounded-md" />
        <Skeleton className="h-[32rem] rounded-md" />
      </div>
    </div>
  );
}
