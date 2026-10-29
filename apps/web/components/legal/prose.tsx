import type { ReactNode } from "react";
import Link from "next/link";

/** Body text for the legal pages: a measure of ~70ch is set by the article, not here. */
export function P({ children }: { children: ReactNode }) {
  return <p className="text-base leading-relaxed">{children}</p>;
}

export function List({ children }: { children: ReactNode }) {
  return <ul className="flex list-disc flex-col gap-2 pl-6 leading-relaxed marker:text-brand-red">{children}</ul>;
}

/** Link styling for a link inside running text on a light surface, focus ring included. */
export const INLINE_LINK =
  "text-link underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";

/** A link to another page of this app, inside running text. */
export function InlineLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link href={href} className={INLINE_LINK}>
      {children}
    </Link>
  );
}

/** An outside link, with the URL kept in the visible text where it matters (the Google permissions page). */
export function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={INLINE_LINK}>
      {children}
    </a>
  );
}

/** A technical value (an OAuth scope, an algorithm) in the mono face. */
export function Code({ children }: { children: ReactNode }) {
  return <code className="rounded-sm bg-muted px-1 font-mono text-[0.875em]">{children}</code>;
}
