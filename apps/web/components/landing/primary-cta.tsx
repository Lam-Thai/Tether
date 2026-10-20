import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The one signup-intent call to action. Signed-out visitors start the trial;
 * signed-in visitors go straight to the dashboard.
 */
export function PrimaryCta({
  signedIn,
  size,
  className,
}: {
  signedIn: boolean;
  size?: "default" | "sm";
  className?: string;
}) {
  return (
    <Link
      href={signedIn ? "/dashboard" : "/sign-up"}
      className={cn(buttonVariants({ size }), className)}
    >
      {signedIn ? "Go to dashboard" : "Start free trial"}
      <ArrowRight aria-hidden />
    </Link>
  );
}
