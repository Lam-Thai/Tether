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
5. **Auth scope.** If the page is business-scoped, confirm it reads the current business from Clerk's session context rather than trusting a route parameter.

## Checklist

- [ ] Uses the typed API client, no raw `fetch` to a provider or the DB.
- [ ] Loading / error / empty states present.
- [ ] Form validation via Zod, matching the backend schema.
- [ ] Success state only rendered after a confirmed API response.
- [ ] No secrets or provider calls in frontend code.
- [ ] Copy uses plain hyphens, colons or two sentences in place of em/en dashes.
- [ ] Any meter or progress bar has `role="meter"` (or equivalent) and states used/limit in text.
