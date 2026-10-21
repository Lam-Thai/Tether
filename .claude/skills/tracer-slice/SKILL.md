---
name: tracer-slice
description: Plan a Tether slice before implementing it. Use when starting work on a GitHub issue labeled type:slice, or any change that should cut through UI, API, a module, and a DB/integration in one PR.
---

# Tracer slice

Tether's issues are tracer-bullet vertical slices, not horizontal layers. This skill is the planning pass that runs **before** the generic `tdd` / `implement` / `to-spec` loop (from `mattpocock-skills`) starts writing tests — it decides what's vertical and names the deep module, so the loop below it has a target.

## Steps

1. **Read the issue.** `gh issue view <n>`. Note the Outcome (the demo that must work end to end) and the Definition of Done.
2. **List the layers touched.** Name every one of `apps/web`, `apps/api/api`, `services`, `repositories`, `models`, `integrations` the slice needs. If it's only one layer, stop and flag it — see "Not actually vertical" below. If it touches `integrations` (any outbound provider call), plan that part with the `provider-adapter` skill.
3. **Design the deep module's interface first.** Before any test or implementation, write down the interface of the slice's central module (the method signatures it exposes, not its internals). Call the `codebase-design` skill for the interface/depth/seam vocabulary if the shape is unclear. A shallow interface here is a sign the slice needs re-cutting, not more code.
4. **Confirm the slice is genuinely vertical.** It should be demoable end to end (a user or caller action produces an observable, correct result) by the time the PR is open — not "the API half, frontend later."
5. **Hand off to the implementation loop.** Use the plugin's `tdd` skill for the red/green cycle, seam by seam, against the interface from step 3.
6. **Run the checks** in the root `CLAUDE.md`'s Commands section for every layer touched.
7. **Update `CLAUDE.md` or the relevant skill** if this slice settled a convention that wasn't written down yet (a new layering decision, a new hard rule, a pattern worth reusing). This is part of the slice, not a follow-up.
8. **Open the PR** with `Closes #<n>`, and include the layers touched and the deep module named in step 3 in the description.

## Not actually vertical

A slice touching exactly one layer (a pure UI tweak, a backend-only internal refactor with no observable change) isn't a tracer slice — say so and either fold it into a slice that does cross layers, or flag it as out-of-process for this skill. Don't force a one-layer change through this checklist just because the issue is labeled `type:slice`.

## Checklist

- [ ] Layers touched are named.
- [ ] The deep module's interface is written down before any test.
- [ ] The slice is demoable end to end, matching the issue's Outcome.
- [ ] Tests exist at the module boundary, plus a cross-tenant test if business data is touched.
- [ ] Every provider call in the slice is metered (`provider-adapter`).
- [ ] `CLAUDE.md` / skills updated if a convention changed.
- [ ] PR links the issue with `Closes #<n>`.
