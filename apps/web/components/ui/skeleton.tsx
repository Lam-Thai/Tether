import { cn } from "@/lib/utils";

/** Placeholder sized by the caller to match the content it stands in for. */
function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("skeleton rounded-sm bg-muted", className)} />;
}

export { Skeleton };
