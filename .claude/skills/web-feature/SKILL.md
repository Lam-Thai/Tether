---
name: web-feature
description: Build a page or section in apps/web. Use when adding a new UI page, form, or data-driven section that calls the backend through the typed API client.
---

# Web feature

## Steps

1. **Data shape.** Confirm the typed API client already has the method this feature needs; if not, that's a backend dependency to resolve first (or in the same slice).
2. **Component.** First apply the root `CLAUDE.md` `## Design` rules (invoke `taste-skill:design-taste-frontend`). Then build with shadcn/ui primitives and Tailwind, following existing component conventions rather than introducing a new pattern.
3. **Form (if any).** Define the Zod schema first, wire it to React Hook Form, and reuse the same schema (or one matching the API's) for both client-side validation and the submit call.
4. **States.** Every data-fetching section needs loading, error, and empty states — not just the happy path. An action (submit, book, send) only shows success after the API call resolves and confirms it.
5. **Auth scope.** If the page is business-scoped, confirm it reads the current business from Clerk's session context rather than trusting a route parameter. A new public page is public only if `lib/routes.ts` `isProtectedRoute` leaves it out: add a case to `lib/routes.test.ts` either way.
6. **Motion.** Product UI (dashboard, auth, billing, legal) uses `animate-enter` only. Marketing-page scroll motion follows `apps/web/DESIGN.md` Resolved decision 16 and its "Where things live" entry: `data-reveal` for reveal-on-scroll, `lib/motion/scroll-driver.ts` for anything pinned or scrubbed (continuous progress goes to a CSS custom property, React state holds discrete steps only), and reduced motion leaves every block visible and unpinned. Photos follow decision 17 (grayscale WebP in `public/landing/`, a row in `CREDITS.md`, `alt=""`).
7. **Legal copy.** If the feature changes what data is collected, sent to a provider, kept or deleted, update `components/legal/privacy-sections.tsx` (and `terms-sections.tsx` for limits, billing or acceptable use) plus `LAST_UPDATED` in `lib/legal.ts` in the same PR (see `apps/web/CLAUDE.md`, "Legal pages").

## Checklist

- [ ] Uses the typed API client, no raw `fetch` to a provider or the DB.
- [ ] Loading / error / empty states present.
- [ ] Form validation via Zod, matching the backend schema.
- [ ] Success state only rendered after a confirmed API response.
- [ ] No secrets or provider calls in frontend code.
- [ ] Copy uses plain hyphens, colons or two sentences in place of em/en dashes.
- [ ] A change to a step of the MVP critical journey (onboarding, agent setup, calendar, test chat, inbox, usage) is reflected in `apps/web/e2e/critical-journey.spec.ts` with accessible locators (`getByRole` / `getByLabel`), and a 429 `rate_limited` from a mutating call is handled.
- [ ] Any meter or progress bar has `role="meter"` (or equivalent) and states used/limit in text.
- [ ] Scroll motion animates only `transform` / `translate` / `opacity`, runs only while its section is on screen, and has a test for the reduced-motion path. Tailwind v4 `translate-*` / `rotate-*` / `scale-*` set the individual CSS properties, so an inline `transform` composes with them and a transition must cover `translate` (`transition-transform` does).
- [ ] Scroll tests in happy-dom: there is no layout (stub `getBoundingClientRect` on elements placed before the effect runs), `window.scrollY` is getter-only (`Object.defineProperty(window, "scrollY", { value, configurable: true })`), and stub `requestAnimationFrame` / `IntersectionObserver` / `matchMedia` with `vi.stubGlobal`. `next/image` static imports arrive as plain strings there, so use `fill` and skip `placeholder="blur"`. Their types come from `types/static-images.d.ts`, not the gitignored `next-env.d.ts` (CI type-checks before any build generates it): a new ambient type a feature needs goes in `types/`.
- [ ] Scroll motion verified in the browser pane with the pane visible (ask the user to show it): a hidden pane runs as a background page, `requestAnimationFrame` may not fire there, so styles a frame callback writes (`--hero-p`, layer opacity/transform) can read stale and an `await` on a frame can hang the script. Readings taken while hidden are not evidence the motion works.
