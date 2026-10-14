# 1. Design direction: minimalist-ui through taste-skill

- Status: Accepted
- Date: 2026-10-13
- Issue: #23

## Context

Most of Tether's UI will be written by agents. Without a fixed direction, agent-generated UI drifts toward the generic "AI dashboard" look: purple-blue gradients, glassy cards nested inside cards, emoji icons, centered heroes, three-up feature grids. Tether is a B2B tool that a salon owner or tradesperson trusts with their phone line, so the UI needs to look calm, dense and credible, and look the same across every session that touches it.

The `taste-skill` Claude Code plugin (`leonxlnx/taste-skill`) provides anti-slop design skills. Until now it was installed only at user scope on one machine, so cloud agents and other contributors didn't get it.

## Decision

- Enable `taste-skill` at project scope in `.claude/settings.json` (`extraKnownMarketplaces` + `enabledPlugins`). `tooling/repo-hygiene.test.ts` checks that it stays enabled.
- Every UI change in `apps/web` first invokes `taste-skill:design-taste-frontend` as the gate.
- The single visual direction is `taste-skill:minimalist-ui`: warm monochrome palette, typographic contrast, flat bento grids, no gradients, no heavy shadows.
- Redesigns and audits use `taste-skill:redesign-existing-projects`.
- taste-skill decides tokens, spacing, type and states on top of shadcn/ui + Tailwind (PRD §3.1). It doesn't replace the component library.

The rules live in the `## Design` section of the root `CLAUDE.md`.

## Alternatives considered

- **`high-end-visual-design` (soft-skill).** Agency-style polish aimed at marketing sites. Its heavier visual treatment competes with dense dashboard data.
- **`industrial-brutalist-ui`.** Distinctive, but its raw terminal aesthetic undermines trust for a small-business audience.
- **`gpt-taste`.** GSAP scroll motion and AIDA page structure are built for landing pages, not an app dashboard.
- **No design skill, only written guidelines.** Cheaper, but prose rules don't get applied consistently across agent sessions. A skill invoked at the right moment does.

## Consequences

- Every contributor and cloud agent gets the same design gate without a manual install. Claude Code prompts to trust the marketplace the first time.
- The direction is a single choice: changing it means superseding this ADR, not mixing skills page by page.
- Supply-chain trade-off: the marketplace tracks the default branch of a third-party repo, and plugin skill text becomes agent instructions. An upstream change reaches every session without review. If that becomes a concern, pin the marketplace to a reviewed commit and bump it deliberately.
- A future marketing landing page may warrant a different skill. If it does, that gets its own ADR, scoped to that surface.
