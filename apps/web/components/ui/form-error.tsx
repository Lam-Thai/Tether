import { CircleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

/** Top-level banner for a failed submit (409 / 422 / network). */
function FormError({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <p
      role="alert"
      className={cn(
        "flex items-start gap-2 rounded-sm border-l-4 border-danger bg-danger/10 px-3 py-2 text-sm text-foreground",
        className,
      )}
    >
      <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-danger-foreground" />
      <span>{children}</span>
    </p>
  );
}

/** Inline, per-field validation message under an `Input`/`Select`. */
function FieldError({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-center gap-1.5 text-sm text-danger-foreground">
      <CircleAlert aria-hidden className="size-3.5 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

export { FormError, FieldError };
