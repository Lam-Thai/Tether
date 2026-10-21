import type { ReactNode } from "react";
import { CircleAlert, CircleCheck, TriangleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

const TONES = {
  danger: { bar: "border-l-danger", icon: "text-danger-foreground", Icon: CircleAlert },
  warning: { bar: "border-l-warning", icon: "text-foreground", Icon: TriangleAlert },
  success: { bar: "border-l-success", icon: "text-foreground", Icon: CircleCheck },
} as const;

/**
 * State panel for empty / warning / error / success. Color is never the
 * only signal: every tone carries an icon and a title (DESIGN.md decision 3).
 */
export function Callout({
  tone,
  title,
  children,
  action,
  headingLevel = 2,
  compact = false,
  className,
}: {
  tone: keyof typeof TONES;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  /** 1 when the callout is the page's main content, so the page keeps an h1. */
  headingLevel?: 1 | 2 | 3;
  /** Tighter padding and a smaller title, for notices that sit inside another panel. */
  compact?: boolean;
  className?: string;
}) {
  const { bar, icon, Icon } = TONES[tone];
  const Heading = `h${headingLevel}` as const;

  return (
    <div
      className={cn(
        "flex rounded-md border border-l-[6px] border-border bg-card shadow-card",
        compact ? "gap-3 p-4" : "gap-4 p-6",
        bar,
        className,
      )}
    >
      <Icon aria-hidden className={cn("shrink-0", compact ? "mt-0.5 size-5" : "mt-1 size-6", icon)} />
      <div className="flex min-w-0 flex-col gap-2">
        <Heading className={cn("font-bold tracking-tight uppercase", compact ? "text-base" : "text-h2")}>{title}</Heading>
        {children && (
          <div className={cn("max-w-[72ch] text-muted-foreground", compact ? "text-sm" : "text-base")}>
            {children}
          </div>
        )}
        {action && <div className="pt-2">{action}</div>}
      </div>
    </div>
  );
}
