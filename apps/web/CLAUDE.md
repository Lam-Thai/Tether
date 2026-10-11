# apps/web

Next.js (App Router) + TypeScript frontend. See the root `CLAUDE.md` for the hard rules — this file only adds conventions specific to this app.

Stub: filled in by the walking-skeleton slice (#2) once the app exists. Expect sections on:

- The typed API client (how it wraps the FastAPI backend, error shape, auth header).
- Clerk integration: where auth state lives, how a page gets the current business.
- Form pattern: React Hook Form + Zod, shared schema between client validation and the API call.
- shadcn/ui + Tailwind conventions (component location, theming).
- Loading / error / empty state pattern for data-fetching pages.

Never call Gemini or Twilio directly from this app — see the root hard rules.
