import Link from "next/link";

import { PrimaryCta } from "@/components/landing/primary-cta";
import { LegalLinks } from "@/components/legal/legal-links";
import { Wordmark } from "@/components/ui/construct";

export function LandingHeader({ signedIn }: { signedIn: boolean }) {
  return (
    <header className="sticky top-0 z-(--z-sticky) border-b border-border bg-background">
      {/* Phones: wordmark + account on the first row, the legal links wrap onto a slim second row
          (they don't fit beside the CTA). From md it is one 64px row. */}
      <div className="mx-auto flex w-full max-w-[1280px] flex-wrap items-center justify-between gap-x-4 gap-y-1 px-6 pt-3 pb-2 md:h-16 md:flex-nowrap md:gap-x-8 md:py-0">
        <Link
          href="/"
          aria-label="Tether home"
          className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          <Wordmark />
        </Link>
        <LegalLinks className="order-last w-full md:order-none md:ml-auto md:w-auto md:text-base" />
        <nav aria-label="Account" className="flex items-center gap-4">
          {signedIn ? null : (
            <Link
              href="/sign-in"
              className="text-base font-medium text-foreground underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              Sign in
            </Link>
          )}
          <PrimaryCta signedIn={signedIn} size="sm" />
        </nav>
      </div>
    </header>
  );
}
