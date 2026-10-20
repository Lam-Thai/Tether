"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@clerk/nextjs";
import { Check } from "lucide-react";

import { startCheckout, type PlanChoice, type PlanRead } from "@/lib/api/client";
import { formatPrice } from "@/lib/billing/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { FormError } from "@/components/ui/form-error";

/**
 * Two ways in: a free trial (no card) or paying now. Either way the click
 * only asks the API for a Stripe Checkout URL and redirects; nothing here
 * claims a plan is active.
 */
export function PlanChooser({ plan, canceled }: { plan: PlanRead; canceled: boolean }) {
  const { getToken } = useAuth();
  const router = useRouter();
  const [pending, setPending] = useState<PlanChoice | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Back from Stripe can restore this page from the bfcache with the buttons
  // still disabled; re-enable them.
  useEffect(() => {
    const reset = (event: PageTransitionEvent) => {
      if (event.persisted) setPending(null);
    };
    window.addEventListener("pageshow", reset);
    return () => window.removeEventListener("pageshow", reset);
  }, []);

  const price = formatPrice(plan.amount, plan.currency);
  const per = `per ${plan.interval}`;

  async function choose(choice: PlanChoice) {
    setError(null);
    setPending(choice);

    const token = await getToken();
    if (!token) {
      setPending(null);
      setError("Your session has expired. Please sign in again.");
      return;
    }

    const result = await startCheckout(token, choice);
    switch (result.kind) {
      case "ok":
        // Keep the buttons disabled while the browser leaves for Stripe.
        window.location.assign(result.url);
        return;
      case "subscription_active":
        router.replace("/dashboard");
        return;
      case "trial_already_used":
        setError("This business has already used its free trial. You can still subscribe.");
        break;
      case "busy":
        setError("A checkout is already being prepared. Give it a moment and try again.");
        break;
      case "no_business":
        router.replace("/onboarding");
        return;
      case "unauthorized":
        setError("Your session has expired. Please sign in again.");
        break;
      case "unreachable":
        setError(result.reason);
        break;
    }
    setPending(null);
  }

  return (
    <div className="flex w-full max-w-4xl flex-col gap-8">
      <div className="flex flex-col gap-3">
        <h1 className="text-h1 font-bold tracking-tight uppercase">Choose how to start</h1>
        <p className="max-w-[60ch] text-base text-muted-foreground">
          One plan with everything in it. You can cancel any time from the billing page.
        </p>
      </div>

      {canceled && (
        <Callout tone="warning" title="Checkout closed">
          Nothing was charged. Pick an option below when you&apos;re ready.
        </Callout>
      )}
      {error && <FormError>{error}</FormError>}

      <div
        className={
          plan.trial_available
            ? "grid grid-cols-1 gap-6 md:grid-cols-[3fr_2fr]"
            : "grid grid-cols-1 gap-6"
        }
      >
        {plan.trial_available && (
          <section
            aria-labelledby="plan-trial"
            className="relative flex flex-col gap-6 overflow-hidden rounded-md border border-border bg-card p-8 shadow-card"
          >
            <div
              aria-hidden
              className="absolute top-0 right-0 h-full w-24 bg-brand-red [clip-path:polygon(60%_0,100%_0,100%_100%,90%_100%)]"
            />
            <Badge tone="warning" className="w-fit">
              {plan.trial_days}-day free trial
            </Badge>
            <div className="flex flex-col gap-2 pr-16">
              <h2 id="plan-trial" className="text-h2 font-bold tracking-tight uppercase">
                Try Tether free
              </h2>
              <p className="text-base text-muted-foreground">
                Then {price} {per}. If you don&apos;t add a card, the trial simply ends.
              </p>
            </div>
            <ul className="flex flex-col gap-2 text-base">
              {["No card required", `${plan.trial_days} days of full access`, "Cancel any time"].map(
                (item) => (
                  <li key={item} className="flex items-center gap-2">
                    <Check aria-hidden className="size-4 shrink-0" />
                    {item}
                  </li>
                ),
              )}
            </ul>
            <Button
              type="button"
              className="mt-auto w-full md:w-fit"
              disabled={pending !== null}
              onClick={() => choose("trial")}
            >
              {pending === "trial" ? "Opening checkout…" : "Start free trial"}
            </Button>
          </section>
        )}

        <section
          aria-labelledby="plan-paid"
          className="flex flex-col gap-6 rounded-md border border-border bg-card p-8 shadow-card"
        >
          <div className="flex flex-col gap-2">
            <h2 id="plan-paid" className="text-h2 font-bold tracking-tight uppercase">
              Subscribe now
            </h2>
            <p className="flex items-baseline gap-2">
              <span className="text-h1 font-bold">{price}</span>
              <span className="text-base text-muted-foreground">{per}</span>
            </p>
          </div>
          {!plan.trial_available && (
            <p className="text-base text-muted-foreground">
              This business has already used its free trial.
            </p>
          )}
          <Button
            type="button"
            variant={plan.trial_available ? "outline" : "default"}
            className="mt-auto w-full md:w-fit"
            disabled={pending !== null}
            onClick={() => choose("paid")}
          >
            {pending === "paid" ? "Opening checkout…" : "Subscribe"}
          </Button>
        </section>
      </div>

      <p className="text-sm text-muted-foreground">
        Payments are handled by Stripe. Tether never sees or stores your card details.
      </p>
    </div>
  );
}
