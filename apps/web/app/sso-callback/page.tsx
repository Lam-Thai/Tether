import { AuthenticateWithRedirectCallback } from "@clerk/nextjs";

import { AuthShell } from "@/components/auth/auth-shell";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Where Google/GitHub send the owner back. Clerk finishes the hand-off: an
 * existing account signs in (→ /dashboard, whose gate routes on), a new one
 * is created (→ /onboarding), and a sign-in for an unknown account becomes
 * a sign-up. Public in middleware, like /sign-in and /sign-up.
 */
export default function SsoCallbackPage() {
  return (
    <AuthShell>
      <div
        role="status"
        aria-live="polite"
        className="flex w-full max-w-sm flex-col gap-4 rounded-md border border-border bg-card p-8 shadow-card"
      >
        <h1 className="text-h2 font-bold tracking-tight uppercase">Finishing sign-in</h1>
        <p className="text-base text-muted-foreground">Confirming your account with the provider.</p>
        <Skeleton className="h-2 w-full" />
      </div>
      {/* Clerk's bot protection mounts here if a new account is created. */}
      <div id="clerk-captcha" />
      <AuthenticateWithRedirectCallback
        signInUrl="/sign-in"
        signUpUrl="/sign-up"
        signInFallbackRedirectUrl="/dashboard"
        signUpFallbackRedirectUrl="/onboarding"
      />
    </AuthShell>
  );
}
