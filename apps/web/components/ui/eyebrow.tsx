import { cn } from "@/lib/utils";

/** Small uppercase label above a heading (DESIGN.md "UI Labels"). */
function Eyebrow({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "text-sm font-medium tracking-[0.08em] text-muted-foreground uppercase",
        className,
      )}
    >
      {children}
    </span>
  );
}

export { Eyebrow };
