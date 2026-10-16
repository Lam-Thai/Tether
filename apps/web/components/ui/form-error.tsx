import { cn } from "@/lib/utils";

/** Top-level, danger-toned banner for a failed submit (409 / 422 / network). */
function FormError({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <p
      role="alert"
      className={cn(
        "rounded-md border border-danger-border bg-danger px-3 py-2 text-sm text-danger-foreground",
        className,
      )}
    >
      {children}
    </p>
  );
}

/** Inline, per-field validation message under an `Input`/`Select`. */
function FieldError({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-danger-foreground">{children}</p>;
}

export { FormError, FieldError };
