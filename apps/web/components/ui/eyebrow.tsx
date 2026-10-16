import { cn } from "@/lib/utils";

/** Small mono-spaced, uppercase "section label" used above a heading. */
function Eyebrow({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase",
        className,
      )}
    >
      {children}
    </span>
  );
}

export { Eyebrow };
