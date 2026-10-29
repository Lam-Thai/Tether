import { clerkMiddleware } from "@clerk/nextjs/server";

import { isProtectedRoute } from "@/lib/routes";

/**
 * Defense in depth only: every protected page also checks the session and
 * `/me` itself, and the API enforces business + plan (402) on its own.
 * Which routes are protected lives in `lib/routes.ts` (tested there).
 */
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
