import { cn } from "@/lib/utils";

/** Text wordmark with a red square: the one fixed brand mark. */
export function Wordmark({ inverse = false, className }: { inverse?: boolean; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 text-2xl font-bold tracking-tight uppercase",
        inverse ? "text-surface-dark-foreground" : "text-foreground",
        className,
      )}
    >
      <span aria-hidden className="size-4 -rotate-12 bg-brand-red" />
      Tether
    </span>
  );
}

/**
 * Decorative constructivist composition on an off-black field: a red
 * diagonal wedge, a blue bar on the opposing angle, a yellow disc. Plain
 * CSS shapes, hidden from assistive tech.
 */
export function ConstructField({ className }: { className?: string }) {
  return (
    <div aria-hidden className={cn("pointer-events-none absolute inset-0 overflow-hidden", className)}>
      <div className="absolute inset-y-0 right-0 w-2/3 bg-brand-red [clip-path:polygon(30%_0,100%_0,100%_100%,78%_100%)]" />
      <div className="absolute top-[42%] -left-10 h-4 w-3/4 -rotate-[24deg] bg-brand-blue md:h-5" />
      <div className="absolute right-[22%] top-[14%] size-14 rounded-full bg-brand-yellow md:size-24" />
      <div className="absolute top-[30%] left-[10%] hidden h-px w-1/2 rotate-[8deg] bg-surface-dark-foreground/60 md:block" />
    </div>
  );
}
