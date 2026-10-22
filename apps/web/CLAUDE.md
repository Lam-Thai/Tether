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
- Tailwind v4, CSS-first config: tokens live in `app/globals.css` under `:root` + `@theme inline`, implementing `DESIGN.md` (see root `CLAUDE.md` `## Design`). No `tailwind.config.ts`.
- Use semantic tokens (`bg-primary`, `text-muted-foreground`, `border-input`, `bg-surface-dark`, ...), never raw hex in components. Fonts: Oswald (`font-sans`), JetBrains Mono (`font-mono`, for technical values).
- States: `Callout` (`components/ui/callout.tsx`) for empty / warning / error panels, `FormError` / `FieldError` for forms, `Skeleton` for loading. Every state pairs color with a Lucide icon and text. Icons come from `lucide-react` only.
- Invoke `taste-skill:design-taste-frontend` before touching any UI.

## Loading / error / empty states

Every data-fetching page needs all three, not just the happy path — see `components/status-view.tsx` for the pattern: a result type with one variant per state, and a presentational component that switches on it exhaustively.

## Testing

Vitest config is scoped to this package (`apps/web/vitest.config.ts`, `happy-dom` environment, `@vitejs/plugin-react`). Tests import `describe`/`it`/`expect` explicitly from `"vitest"` (no `globals: true`); `vitest.setup.ts` registers `@testing-library/jest-dom/vitest` matchers and an `afterEach(cleanup)` since globals are off. Test the typed API client against `global.fetch` (stub success / error-body / thrown-fetch / malformed-JSON cases), and test presentational components by passing each state of the client's result type as a prop rather than mocking the fetch at the component layer.

## Routing and billing

- Signed-in routing has one rule, `lib/billing/destinationFor(me)`: no business -> `/onboarding`, not entitled -> `/billing/plan`, entitled -> `/dashboard`. Every gated page or layout calls `GET /me` (cached per render via `lib/dashboard/current-me.ts`) and redirects with it. Middleware only adds defense in depth; the API enforces the same rule with a 402.
- Payments are redirect-only: the API returns a Stripe Checkout or Portal URL and the client follows it (`https:` only, checked in `lib/api/client.ts`). No Stripe key or Stripe.js in this app.
- Never show a plan as active from the URL: `/billing/return` only says so once `syncCheckout` / `getSubscription` report `entitled`.
- Pages under `/dashboard/*` rely on the dashboard layout's `destinationFor` gate; they handle their own 401/402 results only as a backstop (redirect), never re-implement the gate.

## Agent builder and test chat

- `/dashboard/agent` is the server-loaded page; `components/agent/*` are client components that receive typed server results as props and keep local state after mount.
- The test chat's interaction id lives in `?chat=<uuid>` (`router.replace` after the first send), so a reload restores the transcript from Postgres. Never keep conversation state in `localStorage`.
- Never render an agent reply the API didn't return, and never invent or pad one: no optimistic agent text, no fake typing delay. The customer's message joins the transcript only once the turn response confirms it. A fallback reply is shown as a fallback (`message.fallback`), never as a normal answer.
- After an unknown outcome (timeout/unreachable) on a send, re-fetch the transcript before offering Retry, so a committed turn isn't sent twice. 409 means either `interaction_ended` (show New chat) or `interaction_busy` (keep the draft, retry); branch on `error.code`.
- Feature Zod schemas and limits live in `lib/validation/<feature>.ts` and mirror the API's; PATCH forms send only the changed fields.

- FAQs (`components/agent/faq-manager.tsx`, builder section 4) load via `listFaqs` in parallel with the agent; a failed list renders a Callout in that section only (401/402 redirect in the page). Local state is the source after mount: a FAQ is added, changed, toggled or removed in the list only after the API confirms (create 201, PATCH 200, DELETE 204). The active switch (`role="switch"`) disables itself while in flight and never moves on failure; delete always goes through a confirm dialog.
- Modals use `components/agent/faq-dialog.tsx`: a native `<dialog>` opened with `showModal()` (focus trap, Esc, backdrop click) labelled by its `<h2>`. Children mount only while open, so forms reset on each open. Reuse it rather than adding a dialog dependency.
- `fetchApi` returns `body: null` for 204, so DELETE-style endpoints work; only treat 204 as success.

## Usage limits

- `/dashboard/settings/usage` follows the server page → presentational component pattern (`components/settings/usage-panel.tsx`). `GET /usage` carries this business's meters only; platform-wide state arrives as `global_limit_reached` flags and drives a warning, never a number.
- Meter `kind` is an open string: label known kinds, derive a label for the rest from the string.
- `usage_limited` (turn response) and `concurrency_limited` (429 on a new chat) are state from the latest response only; a reload doesn't restore them.
- Render viewer-local times with `useSyncExternalStore` and a `null` server snapshot (no hydration mismatch); the `react-hooks/set-state-in-effect` lint rule rejects the effect + `setState` version.
- Money arrives as a decimal string: format client-side and label it as an estimate.

## Pending (future slices)

- Clerk integration: where auth state lives, how a page gets the current business.
- Form pattern: React Hook Form + Zod, shared schema between client validation and the API call.
