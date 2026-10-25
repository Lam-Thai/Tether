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
- Builder order (PRD 12.2) in `agent-workspace.tsx`: persona, Business information (`business-info-form.tsx`, PATCH changed fields, blank phone/email send `null`), FAQs, Services (`services-manager.tsx`), Business hours (`hours-editor.tsx`). Each section takes its own GET result and renders `SectionLoadFailure` (`section-shared.tsx`, which also holds the shared `Switch`, `commonFailure`, `SESSION_EXPIRED`) on failure; the page redirects on 401/402 first.
- Money and times stay strings in forms: prices are validated against the business currency's minor digits (`CURRENCIES` in `lib/validation/business.ts`, JPY/KRW/VND have 0) and sent as decimal strings; `lib/format-service.ts` formats with a fixed `en-US` locale. Hours use `HH:MM` in the form and `HH:MM:SS` on the wire; `configured=false` ("Not set yet") is different from closed and its default draft is never shown as saved. Never put regex escapes in a template literal passed to `new RegExp` without checking lint.
- Bookings panel (`components/agent/bookings-panel.tsx`, beside the test chat on wide containers, below on narrow): `GET /bookings?interaction_id=` via `listChatBookings`. `TestChat` owns a `BookingsState` and refreshes it after every successful turn and once on restore; it is cleared on New chat. A response is dropped if the chat changed or a newer refresh started (`currentId` + a sequence ref). No polling. Only `status: "confirmed"` reads as booked; every other status has its own label (`failed` = "Not booked", `needs_reconciliation` = "Needs review"). Date and time come from `date`/`label` (business-local), never converted to the viewer's timezone.
- `fetchApi` returns `body: null` for 204, so DELETE-style endpoints work; only treat 204 as success.

## Inbox

- `/dashboard/inbox` (list) and `/dashboard/inbox/[id]` (detail) follow the server page -> presentational pattern (`components/inbox/inbox-view.tsx`, `detail-view.tsx`). Filters live in the URL only: `lib/inbox/query.ts` parses `searchParams` (anything malformed is dropped), builds hrefs, and `withFilters` always drops `cursor`. Type tabs are links (`aria-current`), the date range is a form that `router.push`es; "Older" uses `next_cursor`, "Back to newest" clears it. A detail link carries the inbox query, so its back link returns to the same filters and page.
- Interaction `status` is `in_progress | completed | failed | abandoned` (also on the test chat's turn and transcript); `ended` stays a separate boolean. Rows mark failed and incomplete conversations with an icon and text (`badges.tsx`); `needsStatusMark` avoids repeating "Failed" / "Abandoned" when the outcome badge already says it.
- Times use the response's business `timezone` through `formatBusinessTime` (fixed `en-US`), never the viewer's. Show only what the API returned: "Summary pending" when `summary` is null, "AI summary" vs "Automatic summary" from `summary_source`, caller "Test chat" / "Unknown caller" when `caller` is null.
- The detail's verified-actions timeline renders `operations` with Lucide `Check` / `X` and the backend `label` verbatim; a non-succeeded action also shows its status word. The transcript (`transcript.tsx`) is a client component: the first page comes from the server, "Load more" appends via `next_after`, repeated sequence numbers are skipped, and a response is dropped if a newer request started. Fallback replies carry a visible note.

## Usage limits

- `/dashboard/settings/usage` follows the server page → presentational component pattern (`components/settings/usage-panel.tsx`). `GET /usage` carries this business's meters only; platform-wide state arrives as `global_limit_reached` flags and drives a warning, never a number.
- Meter `kind` is an open string: label known kinds, derive a label for the rest from the string.
- `usage_limited` (turn response) and `concurrency_limited` (429 on a new chat) are state from the latest response only; a reload doesn't restore them.
- Render viewer-local times with `useSyncExternalStore` and a `null` server snapshot (no hydration mismatch); the `react-hooks/set-state-in-effect` lint rule rejects the effect + `setState` version.
- Money arrives as a decimal string: format client-side and label it as an estimate.

## Calendar

- `/dashboard/settings/calendar` follows the server page → presentational pattern (`components/settings/calendar-panel.tsx`); the nav's Calendar entry links to it and Settings steps aside there. Status is one of `not_connected | needs_selection | connected | reconnect_needed`, and the panel only shows `Connected · "<name>"` from a status the API returned (picker save, status GET), never from a click.
- OAuth is redirect-only: `getCalendarConnectUrl` returns Google's URL and the client only follows `https:` + host exactly `accounts.google.com` (else `invalid_url`). No token, secret or scope list ever reaches this app; it sees the URL and the status payload.
- Google redirects to `/dashboard/settings/calendar/callback` (`components/settings/calendar-callback.tsx`). The server page passes `code`/`state`/`error` as props; the component keeps them in state, strips the URL with `router.replace` at once, and posts `code` + `state` exactly once (a `useRef` guard, because strict mode double-runs effects and a code is single-use). A `?error=` from Google, or a missing code/state, shows a message and makes no API call; the raw error value is never echoed. Failure offers "Try again" back to settings (a new connect), never a replay.
- Disconnect always goes through the confirm dialog. `revoked: false` means the connection is gone but Google did not confirm, so show the warning linking `https://myaccount.google.com/permissions`. A 409 `calendar_reconnect_needed` from the list or save moves the panel to Reconnect needed (the API has already committed that state).
- Error code → copy lives in `components/settings/calendar-messages.ts`; the usage panel labels the `calendar_call` meter "Calendar checks".
- Booking behavior (`components/agent/booking-rules-editor.tsx`, builder section after Business hours): `GET`/`PUT /business/booking-rules`, `customized: false` shows "Using defaults" and is only cleared by the PUT response. Notice is typed in minutes or hours, sent as minutes (`lib/validation/booking-rules.ts`).
- Preview availability (`components/settings/availability-preview.tsx`, shown on the Calendar page only when status is `connected`): `GET /calendar/availability`. Every time shown is a `label` from the response (`formatSlotLabel` only reformats to 12-hour); each `reason` has plain copy in `availability-messages.ts`; a result is cleared when the service or date changes.

## Auth (Clerk custom flows)

- `components/auth/sign-in-form.tsx` / `sign-up-form.tsx` are custom forms over the `@clerk/nextjs/legacy` hooks (no Clerk prebuilt UI). They collect **email + password only**, so the Clerk dashboard must not require anything else (username, names, legal acceptance). If it does, the forms say which field is missing (`components/auth/clerk-status.ts`) and never blame the code or password.
- Branch on Clerk's status, never collapse "not complete" into a generic error: sign-up `missing_requirements` (with `missingFields`); sign-in `needs_client_trust` (a new device) and `needs_second_factor` get the emailed-code step (`prepareSecondFactor` / `attemptSecondFactor` with `email_code`); any other second factor, `needs_new_password` and the rest get a plain message. `setActive` failing is its own message (the account exists; point to sign-in). Navigate only after `setActive` resolves.
- Google / GitHub: `components/auth/oauth-buttons.tsx` calls `signIn` / `signUp.authenticateWithRedirect({ strategy, redirectUrl: "/sso-callback", redirectUrlComplete })`; `app/sso-callback` renders `AuthenticateWithRedirectCallback` (public in middleware). Clerk owns the whole OAuth exchange; no provider keys live in this app. Development instances use Clerk's shared OAuth credentials; production needs the owner's own Google/GitHub OAuth apps configured in the Clerk dashboard. Buttons are text-only (lucide-react has no brand marks).

## Pending (future slices)

- Form pattern: React Hook Form + Zod, shared schema between client validation and the API call.
