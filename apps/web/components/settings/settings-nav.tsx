import Link from "next/link";

import { cn } from "@/lib/utils";

const SECTIONS = [
  { key: "integrations", label: "Integrations", href: "/dashboard/settings/integrations" },
  { key: "usage", label: "Usage", href: "/dashboard/settings/usage" },
  { key: "data", label: "Data", href: "/dashboard/settings/data" },
] as const;

export type SettingsSection = (typeof SECTIONS)[number]["key"];

/** Sub-navigation for the Settings pages. Links, with `aria-current` on the page you are on. */
export function SettingsNav({ current }: { current: SettingsSection }) {
  return (
    <nav aria-label="Settings sections">
      <ul className="flex flex-wrap gap-2">
        {SECTIONS.map((section) => {
          const active = section.key === current;
          return (
            <li key={section.key}>
              <Link
                href={section.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "inline-flex h-9 items-center rounded-md border-[1.5px] px-3 text-sm font-medium tracking-[0.02em] transition-[background-color] duration-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring motion-reduce:transition-none",
                  active
                    ? "border-foreground bg-foreground text-background"
                    : "border-input bg-transparent text-foreground hover:bg-muted",
                )}
              >
                {section.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
