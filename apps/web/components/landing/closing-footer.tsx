import Link from "next/link";

import { PrimaryCta } from "@/components/landing/primary-cta";
import { LegalLinks } from "@/components/legal/legal-links";
import { ConstructField, Wordmark } from "@/components/ui/construct";

export function ClosingCta({ signedIn }: { signedIn: boolean }) {
  return (
    <section className="relative isolate overflow-hidden bg-surface-dark">
      <ConstructField className="-z-10 opacity-0 md:opacity-100" />
      <div className="mx-auto flex w-full max-w-[1280px] flex-col items-start gap-6 px-6 py-[clamp(4rem,8vw,8rem)]">
        <h2 className="max-w-xl text-h1 font-bold tracking-tight text-surface-dark-foreground uppercase md:max-w-sm lg:max-w-md">
          Let the phone ring. Tether has it.
        </h2>
        <PrimaryCta signedIn={signedIn} />
      </div>
    </section>
  );
}

export function LandingFooter() {
  return (
    <footer className="bg-surface-dark text-surface-dark-foreground">
      <div className="mx-auto flex w-full max-w-[1280px] flex-wrap items-center justify-between gap-4 border-t border-surface-dark-muted/30 px-6 py-8">
        <Wordmark inverse />
        <div className="flex flex-wrap items-center gap-6 text-sm text-surface-dark-muted">
          <Link
            href="/status"
            className="underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            System status
          </Link>
          <LegalLinks inverse landmark={false} />
          <span>&copy; 2026 Tether</span>
        </div>
      </div>
    </footer>
  );
}
