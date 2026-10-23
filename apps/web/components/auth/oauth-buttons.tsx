"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";

export type OAuthStrategy = "oauth_google" | "oauth_github";

const PROVIDERS: { strategy: OAuthStrategy; name: string }[] = [
  { strategy: "oauth_google", name: "Google" },
  { strategy: "oauth_github", name: "GitHub" },
];

/**
 * "Continue with Google / GitHub". Clerk owns the whole OAuth hand-off
 * (`authenticateWithRedirect` → provider → `/sso-callback`), so no provider
 * secret or token ever touches this app. `onSelect` resolves `true` once the
 * redirect has started and `false` when it failed (the form shows why).
 * Text-only on purpose: icons come from lucide-react, which has no brand marks.
 */
export function OAuthButtons({
  onSelect,
  disabled = false,
}: {
  onSelect: (strategy: OAuthStrategy) => Promise<boolean>;
  disabled?: boolean;
}) {
  const [pending, setPending] = useState<OAuthStrategy | null>(null);

  async function start(strategy: OAuthStrategy) {
    setPending(strategy);
    // On success the browser is leaving for the provider, so stay disabled.
    if (!(await onSelect(strategy))) setPending(null);
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {PROVIDERS.map(({ strategy, name }) => (
          <Button
            key={strategy}
            type="button"
            variant="outline"
            disabled={disabled || pending !== null}
            onClick={() => void start(strategy)}
          >
            {pending === strategy ? "Redirecting…" : `Continue with ${name}`}
          </Button>
        ))}
      </div>
      <div className="flex items-center gap-3 text-sm text-muted-foreground">
        <span aria-hidden className="h-px flex-1 bg-border" />
        or use your email
        <span aria-hidden className="h-px flex-1 bg-border" />
      </div>
    </div>
  );
}
