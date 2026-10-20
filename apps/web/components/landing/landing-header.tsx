import Link from "next/link";

import { PrimaryCta } from "@/components/landing/primary-cta";
import { Wordmark } from "@/components/ui/construct";

export function LandingHeader({ signedIn }: { signedIn: boolean }) {
  return (
    <header className="sticky top-0 z-(--z-sticky) border-b border-border bg-background">
      <div className="mx-auto flex h-16 w-full max-w-[1280px] items-center justify-between gap-4 px-6">
        <Link
          href="/"
          aria-label="Tether home"
          className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          <Wordmark />
        </Link>
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
