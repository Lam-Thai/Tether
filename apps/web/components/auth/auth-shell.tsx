import Link from "next/link";

import { ConstructField, Wordmark } from "@/components/ui/construct";

/**
 * Split layout shared by sign-in, sign-up and onboarding: a constructivist
 * off-black panel beside the form. Collapses to a short band under 768px.
 */
export function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-[100dvh] grid-cols-1 grid-rows-[auto_1fr] md:grid-cols-[5fr_7fr] md:grid-rows-1">
      <section className="relative isolate flex min-h-36 flex-col justify-between overflow-hidden bg-surface-dark p-6 md:p-10">
        <ConstructField className="-z-10" />
        <Link
          href="/"
          className="w-fit focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <Wordmark inverse />
        </Link>
        {/* Kept narrow so it never reaches the red wedge (white on red is 3.5:1). */}
        <p className="hidden max-w-64 text-h2 font-bold tracking-tight text-surface-dark-foreground uppercase lg:block">
          Answers calls. Books real appointments. Escalates the rest.
        </p>
      </section>
      <main className="flex items-center justify-center p-6 md:p-10">
        <div className="animate-enter flex w-full justify-center">{children}</div>
      </main>
    </div>
  );
}
