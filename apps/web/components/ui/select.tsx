import * as React from "react";

import { cn } from "@/lib/utils";
import { controlClasses } from "@/components/ui/input";

/**
 * A plain native `<select>`, styled to match `Input` - deliberately not a
 * Radix-based combobox. The onboarding form's timezone field needs the
 * full `Intl.supportedValuesOf("timeZone")` list, which a native select
 * handles natively (type-ahead, keyboard nav, accessibility) without an
 * extra dependency.
 */
function Select({ className, ...props }: React.ComponentProps<"select">) {
  return <select data-slot="select" className={cn(controlClasses, className)} {...props} />;
}

export { Select };
