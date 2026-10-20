# 2. Design system: Construtivismo Dinâmico Tech, app-wide

- Status: Accepted
- Date: 2026-10-20
- Issue: #27
- Supersedes: [0001](0001-design-direction-taste-skill.md)

## Context

ADR 0001 fixed `apps/web` to a single visual direction, `taste-skill:minimalist-ui` (warm monochrome, flat, no shadows), and said that changing direction means superseding it rather than mixing styles page by page.

The product now has a specific design system, "Construtivismo Dinâmico Tech" (red / blue / yellow on off-black and white, Oswald, diagonal geometry, 12px radii, subtle motion). It's needed before the marketing landing page and Stripe billing (#28) and before Slice 4 adds the Agent Builder. Switching at that point, with five screens, is the cheapest it will ever be.

The spec as written contradicts itself in places (for example "no pure black" next to a `#000000` token) and several of its color pairings fail WCAG AA.

## Decision

- `apps/web/DESIGN.md` is the single source of truth. It holds the spec verbatim plus a **Resolved decisions** table that overrides the spec wherever the two disagree. The tokens in `apps/web/app/globals.css` implement that table.
- The system applies to the **whole app**: marketing pages and the signed-in product share one set of tokens and primitives.
- `taste-skill:design-taste-frontend` stays as the anti-slop pre-flight gate before any UI change. `taste-skill:minimalist-ui` is no longer the direction and joins the do-not-use list.
- Light mode only for now. Colors are CSS variables, so dark mode is additive later.
- Icons come from `lucide-react` only, because the spec names Lucide.
- `tooling/repo-hygiene.test.ts` fails CI on `h-screen`, pure black, gradients, or emoji in `apps/web/{app,components}`.

## Alternatives considered

- **Keep minimalist-ui for the product, use the new system only on the landing page.** This is what ADR 0001 anticipated. It was rejected because two systems means two sets of primitives (`Button`, `Card`, inputs) drifting apart, and users moving from the landing page into the product would see a jarring style change.
- **Adopt the spec literally.** Rejected because of its internal contradictions and AA failures: primary button text, focus ring, form borders and error text.

## Consequences

- The constructivist palette is loud for a dense dashboard. We hold it back in the product: geometry stays in panels, sidebars and headers, while content areas stay white cards with off-black text.
- Every future UI slice is reviewed against `DESIGN.md`. A new visual need gets a token or a resolved-decision entry there, never a one-off hex value in a component.
- Dark mode is a known gap (decision 4).
