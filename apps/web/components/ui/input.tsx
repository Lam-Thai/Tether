import * as React from "react";

import { cn } from "@/lib/utils";

const controlClasses =
  "flex h-11 w-full rounded-md border border-input bg-card px-3 py-2 text-base text-foreground transition-[border-color] duration-200 placeholder:text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring aria-invalid:border-danger-foreground disabled:cursor-not-allowed disabled:border-disabled disabled:bg-muted motion-reduce:transition-none";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return <input type={type} data-slot="input" className={cn(controlClasses, className)} {...props} />;
}

export { Input, controlClasses };
