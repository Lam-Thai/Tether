import Link from "next/link";

import { PRIVACY_PATH, TERMS_PATH } from "@/lib/legal";
import { cn } from "@/lib/utils";

const LINKS = [
  { label: "Privacy", href: PRIVACY_PATH },
  { label: "Terms", href: TERMS_PATH },
] as const;

/**
 * Privacy and Terms, as a small labelled nav. Used by the landing header and
 * footer, the dashboard sidebar and the legal pages themselves. `inverse` is
 * for off-black surfaces (the focus ring turns yellow there, DESIGN.md 9).
 * The footer passes `landmark={false}`: it is a landmark already, and a page
 * whose header also has these links would otherwise list two "Legal" navs.
 */
export function LegalLinks({
  inverse = false,
  current,
  landmark = true,
  className,
}: {
  inverse?: boolean;
  /** The path of the page showing the links, marked `aria-current`. */
  current?: string;
  landmark?: boolean;
  className?: string;
}) {
  const Wrapper = landmark ? "nav" : "div";
  return (
    <Wrapper
      aria-label={landmark ? "Legal" : undefined}
      className={cn("flex flex-wrap items-center gap-x-6 gap-y-2 text-sm", className)}
    >
      {LINKS.map(({ label, href }) => (
        <Link
          key={href}
          href={href}
          aria-current={current === href ? "page" : undefined}
          className={cn(
            "underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 aria-[current=page]:font-medium aria-[current=page]:underline",
            inverse
              ? "text-surface-dark-muted focus-visible:outline-accent"
              : "text-muted-foreground focus-visible:outline-ring",
          )}
        >
          {label}
        </Link>
      ))}
    </Wrapper>
  );
}
