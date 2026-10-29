import { Check } from "lucide-react";

import { PrimaryCta } from "@/components/landing/primary-cta";

const included = [
  "14-day free trial",
  "No card required",
  "Cancel anytime from the billing portal",
];

// Public marketing copy, so it can't call the authenticated /billing/plan. Keep it in step
// with the Stripe price (STRIPE_PRICE_ID) and BILLING_TRIAL_DAYS when either changes.
export function Pricing({ signedIn }: { signedIn: boolean }) {
  return (
    <section
      id="pricing"
      className="mx-auto grid w-full max-w-[1280px] scroll-mt-28 grid-cols-1 items-center gap-10 px-6 pb-[clamp(4rem,8vw,8rem)] md:grid-cols-[5fr_6fr] md:gap-16"
    >
      <div className="flex flex-col gap-3">
        <h2 className="text-h1 font-bold tracking-tight uppercase">One plan. One price.</h2>
        <p className="max-w-[44ch] text-base text-muted-foreground">
          Everything Tether does is included. Try it for two weeks before you pay anything.
        </p>
      </div>
      <div className="flex flex-col gap-6 rounded-md border border-border bg-card p-8 shadow-card">
        <p className="flex items-baseline gap-2">
          <span className="text-hero font-bold tracking-tight">$29</span>
          <span className="text-lg text-muted-foreground">per month, USD</span>
        </p>
        <ul className="flex flex-col gap-3">
          {included.map((item) => (
            <li key={item} className="flex items-center gap-3">
              <span
                aria-hidden
                className="flex size-6 shrink-0 items-center justify-center rounded-sm bg-brand-green text-foreground"
              >
                <Check className="size-4" />
              </span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
        <PrimaryCta signedIn={signedIn} className="w-full sm:w-fit" />
      </div>
    </section>
  );
}
