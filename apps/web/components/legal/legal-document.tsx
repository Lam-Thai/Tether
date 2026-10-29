import type { ReactNode } from "react";
import Link from "next/link";

import { LandingFooter } from "@/components/landing/closing-footer";
import { LegalLinks } from "@/components/legal/legal-links";
import { Wordmark } from "@/components/ui/construct";
import { formatLegalDate, LAST_UPDATED } from "@/lib/legal";

export type LegalSection = {
  /** The anchor id, stable once published: other pages and Google's review may link to it. */
  id: string;
  title: string;
  body: ReactNode;
};

/**
 * The shared layout for `/privacy` and `/terms`: a plain header, the title
 * with its Last updated date, an anchor table of contents (sticky beside the
 * text from 768px, above it below that) and the sections as h2s in a ~70ch
 * column. A static server component: it never reads the session, so both
 * pages render the same signed in or out.
 */
export function LegalDocument({
  title,
  summary,
  path,
  sections,
}: {
  title: string;
  summary: string;
  /** This page's path, marked current in the header links. */
  path: string;
  sections: LegalSection[];
}) {
  return (
    <>
      <header className="sticky top-0 z-(--z-sticky) border-b border-border bg-background">
        <div className="mx-auto flex h-16 w-full max-w-[1280px] items-center justify-between gap-4 px-6">
          <Link
            href="/"
            aria-label="Tether home"
            className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <Wordmark />
          </Link>
          <LegalLinks current={path} className="text-base" />
        </div>
      </header>
      <main className="animate-enter mx-auto w-full max-w-[1280px] px-6 py-[clamp(3rem,6vw,5rem)]">
        <div className="flex max-w-[70ch] flex-col gap-4">
          <span aria-hidden className="block h-2 w-16 -skew-x-12 bg-brand-red" />
          <h1 className="text-h1 font-bold tracking-tight uppercase">{title}</h1>
          <p className="text-lg leading-relaxed text-muted-foreground">{summary}</p>
          <p className="font-mono text-sm text-muted-foreground">
            Last updated <time dateTime={LAST_UPDATED}>{formatLegalDate(LAST_UPDATED)}</time>
          </p>
        </div>
        <div className="mt-12 grid grid-cols-1 gap-10 md:grid-cols-[14rem_minmax(0,1fr)] md:gap-16">
          <nav aria-label="On this page" className="md:sticky md:top-24 md:self-start">
            <p className="text-sm font-medium tracking-[0.08em] text-muted-foreground uppercase">On this page</p>
            <ol className="mt-3 flex flex-col gap-2 border-l-4 border-border pl-4 text-sm">
              {sections.map((section) => (
                <li key={section.id}>
                  <a
                    href={`#${section.id}`}
                    className="text-foreground underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  >
                    {section.title}
                  </a>
                </li>
              ))}
            </ol>
          </nav>
          <article className="flex max-w-[70ch] flex-col gap-12">
            {sections.map((section) => (
              <section
                key={section.id}
                id={section.id}
                aria-labelledby={`${section.id}-heading`}
                className="flex scroll-mt-24 flex-col gap-4"
              >
                <h2 id={`${section.id}-heading`} className="text-h2 font-bold tracking-tight uppercase">
                  {section.title}
                </h2>
                {section.body}
              </section>
            ))}
          </article>
        </div>
      </main>
      <LandingFooter />
    </>
  );
}
