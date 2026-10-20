import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/**
 * Primary is red with off-black text: #111 on #FF4136 is ~5.5:1, where the
 * spec's #333 and white both fail AA (DESIGN.md, resolved decision 2).
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-base font-semibold tracking-[0.02em] transition-[background-color,border-color,box-shadow,transform] duration-200 ease-out focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring active:translate-y-px disabled:pointer-events-none disabled:opacity-50 motion-reduce:transition-none motion-reduce:active:translate-y-0 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground hover:bg-primary-hover hover:shadow-lift",
        outline:
          "border-[1.5px] border-input bg-transparent text-foreground hover:bg-muted",
        destructive:
          "border-[1.5px] border-danger-foreground bg-transparent text-danger-foreground hover:bg-danger/10",
        inverse:
          "border-[1.5px] border-surface-dark-muted bg-transparent text-surface-dark-foreground hover:bg-white/10 focus-visible:outline-accent",
      },
      size: {
        default: "h-11 px-5",
        sm: "h-9 px-3 text-sm",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ComponentProps<"button">,
    VariantProps<typeof buttonVariants> {}

function Button({ className, variant, size, ...props }: ButtonProps) {
  return (
    <button
      data-slot="button"
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  );
}

export { Button, buttonVariants };
