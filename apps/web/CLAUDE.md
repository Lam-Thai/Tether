# apps/web

Next.js (App Router) + TypeScript (strict) frontend. See the root `CLAUDE.md` for the hard rules — this file only adds conventions specific to this app.

Stack: Next.js 15 (App Router, Turbopack), React 19, Tailwind CSS v4, shadcn/ui-style components, Vitest + React Testing Library. Clerk and React Hook Form + Zod land in a later slice (out of scope for the walking skeleton, #2) — sections below are stubs until then.

## Commands

From `apps/web/`: `pnpm dev`, `pnpm build`, `pnpm lint` (ESLint flat config), `pnpm typecheck` (`tsc --noEmit`), `pnpm test` (Vitest, this package only).

## The typed API client

`lib/api/client.ts` is the *only* place that reads `NEXT_PUBLIC_API_BASE_URL` — don't read `process.env` for it anywhere else. Each backend endpoint gets a function here that returns a typed, discriminated result (e.g. `HealthResult`), so pages never write their own try/catch around `fetch`. Never call Gemini, Twilio, or Google Calendar directly from this app; everything goes through FastAPI.

Convention for data-fetching pages: Server Component does the fetch via the client and passes the typed result into a presentational component (e.g. `app/page.tsx` -> `components/status-view.tsx`) so the loading/error/empty rendering logic can be unit-tested without a Server Component boundary.

## shadcn/ui + Tailwind conventions

- `components/ui/*` holds shadcn-style primitives (`cn()` in `lib/utils.ts`, `class-variance-authority` for variants). `components.json` records the CLI config (`new-york` style, `@/components`, `@/lib`, `@/hooks` aliases) so `pnpm dlx shadcn add <component>` drops files in the right place.
- Tailwind v4, CSS-first config: tokens live in `app/globals.css` under `:root` + `@theme inline` (warm monochrome palette — see root `CLAUDE.md` `## Design`). No `tailwind.config.ts`.
- Design direction is `taste-skill:minimalist-ui`: flat cards (hairline `border-border`, no `shadow-*`), no gradients, one warm accent per non-happy state (`warn` = ochre, `danger` = terracotta) instead of saturated red/green alerts. Invoke `taste-skill:design-taste-frontend` before touching any UI.

## Loading / error / empty states

Every data-fetching page needs all three, not just the happy path — see `components/status-view.tsx` for the pattern: a result type with one variant per state, and a presentational component that switches on it exhaustively.

## Testing

Vitest config is scoped to this package (`apps/web/vitest.config.ts`, `happy-dom` environment, `@vitejs/plugin-react`). Tests import `describe`/`it`/`expect` explicitly from `"vitest"` (no `globals: true`); `vitest.setup.ts` registers `@testing-library/jest-dom/vitest` matchers and an `afterEach(cleanup)` since globals are off. Test the typed API client against `global.fetch` (stub success / error-body / thrown-fetch / malformed-JSON cases), and test presentational components by passing each state of the client's result type as a prop rather than mocking the fetch at the component layer.

## Pending (future slices)

- Clerk integration: where auth state lives, how a page gets the current business.
- Form pattern: React Hook Form + Zod, shared schema between client validation and the API call.
