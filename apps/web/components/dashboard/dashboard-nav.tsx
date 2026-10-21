"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

/** Sections with a page. Every other entry is a future slice, so it renders as a non-interactive item. */
const NAV_ITEMS = [
  { label: "Agent", href: "/dashboard/agent" },
  { label: "FAQs" },
  { label: "Services" },
  { label: "Calendar" },
  { label: "Inbox" },
  { label: "Phone" },
  { label: "Settings" },
] as const;

export function DashboardNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Workspace sections">
      <ul className="flex flex-wrap gap-1 md:flex-col">
        {NAV_ITEMS.map((item) => {
          if (!("href" in item)) {
            return (
              <li key={item.label}>
                <span
                  aria-disabled="true"
                  className="block cursor-not-allowed rounded-sm border-l-4 border-transparent px-3 py-1.5 text-base text-surface-dark-muted"
                >
                  {item.label}
                </span>
              </li>
            );
          }

          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <li key={item.label}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "block rounded-sm border-l-4 px-3 py-1.5 text-base text-surface-dark-foreground transition-[background-color] duration-200 hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent motion-reduce:transition-none",
                  active ? "border-accent font-medium" : "border-transparent",
                )}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
