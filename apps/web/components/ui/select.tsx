import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * A plain native `<select>`, styled to match `Input` — deliberately not a
 * Radix-based combobox. The onboarding form's timezone field needs the
 * full `Intl.supportedValuesOf("timeZone")` list, which a native select
 * handles natively (type-ahead, keyboard nav, accessibility) without an
 * extra dependency.
 */
function Select({ className, ...props }: React.ComponentProps<"select">) {
  return (
    <select
      data-slot="select"
      className={cn(
        "flex h-10 w-full rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground outline-none transition-colors focus-visible:border-foreground/40 focus-visible:ring-2 focus-visible:ring-foreground/10 disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export { Select };
