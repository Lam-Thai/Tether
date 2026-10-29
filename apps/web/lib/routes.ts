import { createRouteMatcher } from "@clerk/nextjs/server";

/**
 * The routes middleware sends to sign-in. Everything else is public: the
 * landing page, `/status`, `/privacy`, `/terms`, sign-in, sign-up and
 * `/sso-callback`. Google's OAuth review needs the privacy policy reachable
 * signed out, so never widen this to a catch-all.
 */
export const isProtectedRoute = createRouteMatcher([
  "/dashboard(.*)",
  "/onboarding(.*)",
  "/billing(.*)",
]);
