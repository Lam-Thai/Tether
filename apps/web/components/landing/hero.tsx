import Link from "next/link";

import { PrimaryCta } from "@/components/landing/primary-cta";
import { buttonVariants } from "@/components/ui/button";
import { ConstructField } from "@/components/ui/construct";

export function Hero({ signedIn }: { signedIn: boolean }) {
  return (
    <section className="mx-auto grid w-full max-w-[1280px] grid-cols-1 items-stretch gap-10 px-6 py-10 md:min-h-[calc(100dvh-4rem)] md:grid-cols-[7fr_5fr] md:items-center md:gap-12 md:py-12">
      <div className="flex flex-col gap-6">
        {/* Scaled so the headline stays within two lines at desktop widths. */}
        <h1 className="animate-enter text-[clamp(2.25rem,4.2vw,3.5rem)] leading-[1.05] font-bold tracking-tight uppercase">
          Stop choosing between your work and your phone
        </h1>
        <p
          className="animate-enter max-w-[48ch] text-lg text-muted-foreground"
          style={{ "--i": 1 } as React.CSSProperties}
        >
          Tether answers calls and chats, and books real appointments for you.
        </p>
        <div
          className="animate-enter flex flex-wrap items-center gap-3"
          style={{ "--i": 2 } as React.CSSProperties}
        >
          <PrimaryCta signedIn={signedIn} />
          <Link href="#how-it-works" className={buttonVariants({ variant: "outline" })}>
            See how it works
          </Link>
        </div>
      </div>
      <div className="relative isolate min-h-64 overflow-hidden rounded-lg bg-surface-dark md:min-h-[28rem]">
        <ConstructField className="-z-10" />
      </div>
    </section>
  );
}
