import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

/**
 * Defense in depth only: every protected page also checks the session and
 * `/me` itself, and the API enforces business + plan (402) on its own.
 * Public: the landing page, `/status`, sign-in, sign-up.
 */
const isProtectedRoute = createRouteMatcher([
  "/dashboard(.*)",
  "/onboarding(.*)",
  "/billing(.*)",
]);

export default clerkMiddleware(async (auth, request) => {
  if (isProtectedRoute(request)) {
    await auth.protect();
  }
});

export const config = {
  matcher: [
    // Skip Next.js internals and all static files, unless found in search params.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API routes.
    "/(api|trpc)(.*)",
  ],
};
