import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/** Fills carry the color; text stays off-black so every tone passes AA. */
const badgeVariants = cva(
  "inline-flex w-fit items-center gap-1.5 rounded-sm px-2.5 py-1 text-sm font-medium tracking-[0.04em] whitespace-nowrap uppercase [&_svg]:size-3.5",
  {
    variants: {
      tone: {
        neutral: "bg-muted text-foreground",
        success: "bg-success text-success-foreground",
        warning: "bg-warning text-warning-foreground",
        danger: "bg-danger text-foreground",
      },
    },
    defaultVariants: {
      tone: "neutral",
    },
  },
);

export interface BadgeProps
  extends React.ComponentProps<"span">,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, tone, ...props }: BadgeProps) {
  return <span data-slot="badge" className={cn(badgeVariants({ tone }), className)} {...props} />;
}

export { Badge, badgeVariants };
