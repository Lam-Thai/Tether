import Link from "next/link";
import { Check, X } from "lucide-react";

import type { ReadinessItem } from "@/lib/api/client";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { readinessLabel } from "./integration-labels";

/**
 * The activation checklist as a list: Check or X, the API's own message, and a
 * fix link on every step that is not done. Used by the home guide, the
 * integrations page and the Agent Builder's Activation section.
 */
export function ReadinessList({ items, label }: { items: ReadinessItem[]; label: string }) {
  return (
    <ul aria-label={label} className="flex flex-col divide-y divide-border rounded-md border border-border bg-card">
      {items.map((item) => (
        <li
          key={item.key}
          className="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 gap-y-2 p-4 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center"
        >
          {item.ok ? (
            <Check aria-hidden className="mt-0.5 size-5 shrink-0 sm:mt-0" />
          ) : (
            <X aria-hidden className="mt-0.5 size-5 shrink-0 text-danger-foreground sm:mt-0" />
          )}
          <div className="flex min-w-0 flex-col">
            <span className="text-base font-semibold">
              {readinessLabel(item.key)}
              <span className="sr-only">{item.ok ? ": done" : ": not done"}</span>
              {!item.required && <span className="ml-2 text-sm font-normal text-muted-foreground">Optional</span>}
            </span>
            <span className={cn("text-sm", item.ok ? "text-muted-foreground" : "text-foreground")}>
              {item.message}
            </span>
          </div>
          {!item.ok && (
            <Link
              href={item.fix_url}
              aria-label={`Fix ${readinessLabel(item.key)}`}
              className={cn(
                buttonVariants({ variant: "outline", size: "sm" }),
                "col-start-2 w-fit sm:col-start-auto",
              )}
            >
              Fix
            </Link>
          )}
        </li>
      ))}
    </ul>
  );
}
