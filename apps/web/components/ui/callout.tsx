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
  className,
}: {
  tone: keyof typeof TONES;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  /** 1 when the callout is the page's main content, so the page keeps an h1. */
  headingLevel?: 1 | 2 | 3;
  className?: string;
}) {
  const { bar, icon, Icon } = TONES[tone];
  const Heading = `h${headingLevel}` as const;

  return (
    <div
      className={cn(
        "flex gap-4 rounded-md border border-l-[6px] border-border bg-card p-6 shadow-card",
        bar,
        className,
      )}
    >
      <Icon aria-hidden className={cn("mt-1 size-6 shrink-0", icon)} />
      <div className="flex min-w-0 flex-col gap-2">
        <Heading className="text-h2 font-bold tracking-tight uppercase">{title}</Heading>
        {children && <div className="max-w-[72ch] text-base text-muted-foreground">{children}</div>}
        {action && <div className="pt-2">{action}</div>}
      </div>
    </div>
  );
}
