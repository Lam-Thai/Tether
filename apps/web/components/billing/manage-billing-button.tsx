"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";

import { openBillingPortal } from "@/lib/api/client";
import { Button, type ButtonProps } from "@/components/ui/button";
import { FieldError } from "@/components/ui/form-error";

/** Opens the Stripe Billing Portal (update card, cancel, invoices). */
export function ManageBillingButton({ variant = "outline" }: { variant?: ButtonProps["variant"] }) {
  const { getToken } = useAuth();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const reset = (event: PageTransitionEvent) => {
      if (event.persisted) setPending(false);
    };
    window.addEventListener("pageshow", reset);
    return () => window.removeEventListener("pageshow", reset);
  }, []);

  async function open() {
    setError(null);
    setPending(true);
    const token = await getToken();
    const result = token ? await openBillingPortal(token) : ({ kind: "unauthorized" } as const);

    if (result.kind === "ok") {
      window.location.assign(result.url);
      return;
    }
    setPending(false);
    setError(
      result.kind === "unreachable"
        ? result.reason
        : result.kind === "not_found"
          ? "There is no billing account for this business yet."
          : "Your session has expired. Please sign in again.",
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <Button type="button" variant={variant} size="sm" disabled={pending} onClick={open}>
        {pending ? "Opening billing…" : "Manage billing"}
      </Button>
      {error && <FieldError>{error}</FieldError>}
    </div>
  );
}
