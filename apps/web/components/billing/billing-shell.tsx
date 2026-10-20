import Link from "next/link";

import { SignOutButton } from "@/components/dashboard/sign-out-button";
import { Wordmark } from "@/components/ui/construct";

/** Page frame for the signed-in, pre-dashboard billing steps. */
export function BillingShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto flex min-h-[100dvh] w-full max-w-[1280px] flex-col px-6">
      <header className="flex h-16 items-center justify-between gap-4">
        <Link
          href="/"
          className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          <Wordmark />
        </Link>
        <SignOutButton variant="outline" />
      </header>
      <main className="animate-enter flex flex-1 justify-center py-[clamp(2rem,6vw,5rem)]">
        {children}
      </main>
    </div>
  );
}
