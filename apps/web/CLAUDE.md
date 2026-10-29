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
- Voice (slice 15, `components/agent/voice-field.tsx`, in the persona card): the select lists only what `GET /agent/voices` returned (`listVoices`, loaded with the page; a failure shows inline and the rest of the form still works) and saves through the persona's Save as a changed field. "Preview voice" (`previewVoice`, its own binary fetch path, not `fetchApi`) plays the SAVED greeting in the selected voice via an object URL (revoked on replace/unmount); `ok` only for a non-empty `audio/*` 200. `standard` and `tts_configured: false` disable it (the latter with a "Demo mode" note: calls use the standard voice). A 429 `usage_limit_reached` says the daily limit resets at 00:00 UTC. Never claim calls use a voice unless `tts_configured` is true.

## Inbox

- `/dashboard/inbox` (list) and `/dashboard/inbox/[id]` (detail) follow the server page -> presentational pattern (`components/inbox/inbox-view.tsx`, `detail-view.tsx`). Filters live in the URL only: `lib/inbox/query.ts` parses `searchParams` (anything malformed is dropped), builds hrefs, and `withFilters` always drops `cursor`. Type tabs are links (`aria-current`), the date range is a form that `router.push`es; "Older" uses `next_cursor`, "Back to newest" clears it. A detail link carries the inbox query, so its back link returns to the same filters and page.
- Interaction `status` is `in_progress | completed | failed | abandoned` (also on the test chat's turn and transcript); `ended` stays a separate boolean. Rows mark failed and incomplete conversations with an icon and text (`badges.tsx`); `needsStatusMark` avoids repeating "Failed" / "Abandoned" when the outcome badge already says it.
- Times use the response's business `timezone` through `formatBusinessTime` (fixed `en-US`), never the viewer's. Show only what the API returned: "Summary pending" when `summary` is null, "AI summary" vs "Automatic summary" from `summary_source`, caller "Test chat" / "Unknown caller" when `caller` is null.
- The detail's verified-actions timeline renders `operations` with Lucide `Check` / `X` and the backend `label` verbatim; a non-succeeded action also shows its status word. The transcript (`transcript.tsx`) is a client component: the first page comes from the server, "Load more" appends via `next_after`, repeated sequence numbers are skipped, and a response is dropped if a newer request started. Fallback replies carry a visible note.
- Escalations (slice 13): rows with `needs_attention` (an OPEN escalation) show "Needs attention · <reason>"; the old "Needs a person" badge is only for legacy `requires_human` rows with no escalation. The "Needs attention" tab is `type=attention`. The detail's `escalation-panel.tsx` lists `detail.escalations` (reason, urgent mark, SMS status label, `sms_error` as "Error code: X") with a Resolve button that updates in place only after a confirmed 200; a small client provider in that file keeps the header badge and the panel in step. Reason, urgency and SMS status are open strings: unknown values get a derived label (reason) or "Unknown" (SMS status), never a crash.
- Agent Builder section "Escalation rules" (`components/agent/escalation-rules-editor.tsx`, `lib/validation/escalation.ts`): contact phone (E.164, required when text alerts are on), text alerts toggle, clarification attempts 1-5, urgent keywords (<= 20, 1-40 chars, unique ignoring case; Enter adds a keyword, never submits). `sms_configured: false` shows the demo-mode note (alerts are recorded, nothing is texted); a Twilio trial can only text verified numbers.

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

## Phone

- `/dashboard/phone` (nav "Phone", between Calendar and Inbox) follows the server page -> presentational pattern (`components/settings/phone-panel.tsx`, copy in `phone-messages.ts`, client functions `getPhoneNumbers` / `associatePhoneNumber` / `testPhoneNumber` / `disconnectPhoneNumber`). The page shows only what `GET /phone-numbers` returned: the business's numbers (`Connected · webhooks verified` only for `status: "connected"` with a `verified_at`; `misconfigured` explains its `last_error`) and the account's UNASSIGNED numbers (the API never returns another business's). A number joins or leaves the list only after the API confirms (201 / 200).
- There is no purchase button or purchase wording anywhere (the API can't buy numbers; a test asserts no /buy|purchase/ text). `configured: false` is a calm demo-mode callout pointing to the Test chat, not an error. The trial notice shows whenever `trial` is true or unknown: verified caller IDs only, Twilio's trial message first, turn-based calls.
- Disconnect always goes through the confirm dialog; `webhooks_cleared: false` means the row is gone but the Voice URL may still be set on Twilio, so show the warning linking the Twilio console. `phone_number_in_use` copy never says which business holds the number.
- Inbox lists show a voice caller ID masked to its last four digits (the API masks it); the detail view shows the full number.

## Dashboard, integrations and activation (slice 16)

- `/dashboard` (route group `app/dashboard/(overview)/`, so its `loading.tsx` stays off the other dashboard routes) loads `getDashboardSummary`, `getIntegrationStatus` and `listInteractions(limit: 5)` in parallel with `/me`; `components/dashboard/dashboard-home.tsx` gives each block its own result, so one failure is a Callout in that block only (401/402 redirect in the page first). Nav has an "Overview" entry (exact match on `/dashboard`).
- Stat tiles (`summary-tiles.tsx`): numbers in `font-mono`; the range label comes from the response's `from`/`to` (plain dates, never converted). "Needs attention" is the open queue, not range-bound, and links to `/dashboard/inbox?type=attention`.
- Status cards, the errors list and usage meters (`integration-status.tsx`) are shared by the home page and `/dashboard/settings/integrations` (`detailed` adds last-checked and verified times). Connection `status`, error `source` and `code` are open strings: `integration-labels.ts` gives known ones plain copy (calendar `reconnect_needed` reads "Reconnect needed") and derives a label for the rest. Error times use the business timezone from `/me`.
- The "Get set up" guide (`setup-guide.tsx`) shows only when BOTH the integrations and the recent-conversations fetches succeeded, there are no conversations and the agent is inactive. Its items are the API's readiness items (`readiness-list.tsx`: Check / X, the API's own message, a Fix link on open steps). `fix_url` must be a same-site path (`isSitePath` in the client rejects anything else), and unknown item keys still render.
- Settings sub-nav (`settings-nav.tsx`: Integrations, Usage, Data) is rendered by each page; `/dashboard/settings` redirects to integrations. The nav's Settings entry covers all of them, Calendar keeps its own entry.
- `/dashboard/settings/data` (`test-data-panel.tsx`): one danger zone and a confirm dialog (`faq-dialog.tsx`) listing what is deleted and kept. The button enables only on the exact phrase `DELETE TEST DATA` (trimmed, like the API, which still decides). It disables while in flight (a ref guards double submit), and counts appear only from the 200. `calendar_events_not_cancelled > 0` shows a warning to remove them in Google Calendar. A 422 `confirmation_mismatch` says nothing was deleted; an unknown outcome never claims success.
- Account deletion (`components/settings/account-deletion-panel.tsx`, `deleteAccount` in the client, `DELETE /account` on `get_tenant`, so no plan needed): a `card` under the test-data panel on Settings > Data, and a low-emphasis `disclosure` on `/billing/plan` and `/onboarding` (the pages owners without a plan or a business land on). Typed phrase `DELETE MY ACCOUNT`. A 503 `billing_unreachable` means nothing was deleted. Sign out only after a 200 with `sign_in_deleted: true` and nothing left for the owner to fix by hand; `sign_in_deleted: false` keeps them signed in with Try again (the endpoint is idempotent) so the sign-in can still be removed.
- Agent Builder: `agent-workspace.tsx` wraps sections in anchors `#persona`, `#services`, `#hours`, `#escalation`, `#test-chat` and `#activation` (the fix-link targets). `getAgentReadiness` loads with the page; `activation-checklist.tsx` (full-width below the builder and the chat) shows the items, Activate (disabled unless `ready`), Deactivate (confirm dialog: callers hear that you're not available) and "Check again". `active` and the items change only from an API response; a 409 `agent_not_ready` keeps it inactive, shows the API's message and re-fetches readiness. The checklist can go stale while the owner edits other sections, so Check again is manual and Activate always re-checks on the server.
- The test chat works while the agent is inactive, and the card title stays "Test your receptionist".

## Auth (Clerk custom flows)

- `components/auth/sign-in-form.tsx` / `sign-up-form.tsx` are custom forms over the `@clerk/nextjs/legacy` hooks (no Clerk prebuilt UI). They collect **email + password only**, so the Clerk dashboard must not require anything else (username, names, legal acceptance). If it does, the forms say which field is missing (`components/auth/clerk-status.ts`) and never blame the code or password.
- Branch on Clerk's status, never collapse "not complete" into a generic error: sign-up `missing_requirements` (with `missingFields`); sign-in `needs_client_trust` (a new device) and `needs_second_factor` get the emailed-code step (`prepareSecondFactor` / `attemptSecondFactor` with `email_code`); any other second factor, `needs_new_password` and the rest get a plain message. `setActive` failing is its own message (the account exists; point to sign-in). Navigate only after `setActive` resolves.
- Google / GitHub: `components/auth/oauth-buttons.tsx` calls `signIn` / `signUp.authenticateWithRedirect({ strategy, redirectUrl: "/sso-callback", redirectUrlComplete })`; `app/sso-callback` renders `AuthenticateWithRedirectCallback` (public in middleware). Clerk owns the whole OAuth exchange; no provider keys live in this app. Development instances use Clerk's shared OAuth credentials; production needs the owner's own Google/GitHub OAuth apps configured in the Clerk dashboard. Buttons are text-only (lucide-react has no brand marks).

## Legal pages

- `/privacy` and `/terms` (`app/privacy`, `app/terms`) are static server components: they never call `auth()`, so they render the same signed in or out, and `lib/routes.ts` `isProtectedRoute` (the middleware's matcher, tested in `lib/routes.test.ts`) must keep them public. Google's OAuth verification links to `/privacy` signed out.
- Both use `components/legal/legal-document.tsx` (`LegalDocument`: header, h1, Last updated, anchor table of contents, h2 sections in a ~70ch column, the landing footer). Content is a `LegalSection[]` (`privacy-sections.tsx`, `terms-sections.tsx`) built from `prose.tsx` (`P`, `List`, `ExternalLink`, `Code`). Section `id`s are anchors other pages and Google may link to: don't rename them.
- Legal facts (`LEGAL_NAME`, `CONTACT_EMAIL`, `JURISDICTION`, `LAST_UPDATED`) live only in `lib/legal.ts`; the owner fills them in. Render them with `LegalFact`, which shows `[label: to be confirmed]` for a `null`. Never write a company name, address, email or jurisdiction into the copy.
- The copy must match what Tether actually does. A slice that changes what is collected, what reaches a provider (Gemini, Google, Stripe, Twilio), what is kept or how it is deleted updates `privacy-sections.tsx` / `terms-sections.tsx` and `LAST_UPDATED` in the same PR. The usage limits and trial length in the Terms follow the API defaults (`apps/api/core/config.py`, `BILLING_TRIAL_DAYS`). The phone and SMS section stays a "not active yet" placeholder until Twilio is live.
- `legal.test.tsx` pins Google's Limited Use sentence word for word, the three Calendar scopes with their purposes and the "never used for" list. Don't loosen those assertions to make an edit pass.
- `LegalLinks` (`components/legal/legal-links.tsx`, a `<nav aria-label="Legal">`, `inverse` on off-black) is the one set of Privacy / Terms links: landing header (a slim second row on phones, one row from md; the landing anchors use `scroll-mt-28` to clear the taller phone header), landing footer (`landmark={false}`), dashboard sidebar (`components/dashboard/sidebar-footer.tsx`, under Sign out) and the legal pages' header. The sign-up form's consent line sits below both the provider buttons and the email form.

## Pending (future slices)

- Form pattern: React Hook Form + Zod, shared schema between client validation and the API call.
