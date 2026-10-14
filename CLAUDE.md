# Tether

Tether is an AI receptionist for small businesses: it answers calls and chats, books real appointments, and escalates what it can't handle, so an owner never has to choose between running their business and answering the phone.

**Gemini understands the customer. FastAPI validates the intent. PostgreSQL stores the state. Google Calendar confirms availability. Twilio communicates with the caller.** Gemini never acts directly — it asks FastAPI, which validates, persists, and calls the real provider before anything is confirmed back to the customer.

## Repo map

- `apps/web` — Next.js + TypeScript, Tailwind, shadcn/ui, React Hook Form + Zod, Clerk. See `apps/web/CLAUDE.md`.
- `apps/api` — FastAPI. Layers, each only calling the one below it:
  - `api` — routers; request/response wiring only, no business logic.
  - `core` — settings, auth, errors, cross-cutting concerns (`UsageGuard`, `TenantContext`).
  - `schemas` — Pydantic request/response models.
  - `services` — business logic; the deep modules. No FastAPI or SQLAlchemy imports.
  - `repositories` — the only layer that queries the DB; every query scoped by `business_id`.
  - `models` — SQLAlchemy ORM models.
  - `integrations` — Gemini, Twilio, Google Calendar clients, each behind a port with an in-memory fake.
  - `tests` — mirrors the layers above.
  See `apps/api/CLAUDE.md`.

## Hard rules

- Never trust a client-supplied `business_id`; always resolve it from the authenticated tenant (`TenantContext`).
- Never tell a caller something succeeded (booking, SMS, any external call) unless a backend result confirms it.
- Never keep conversation state in Python globals or in memory; all state lives in Postgres.
- No secrets in frontend code, and no Gemini or Twilio calls from the frontend.
- Model IDs and usage limits come from settings and are never hardcoded.
- Use synthetic data only and never commit real customer data.
- Every outbound provider call goes through `UsageGuard` and uses bounded retries.

## How we build

Tracer-bullet vertical slices: one PR carries a change through UI → API → module → DB/integration, never just one layer. Each slice is built around a **deep module** — a small interface hiding a lot of logic — reached through ports and adapters, with an in-memory fake standing in for every external provider in tests. Tests sit at the module boundary, not against internals.

For the generic "ticket → tests → implementation" loop, use the `mattpocock-skills` plugin's `tdd`, `implement`, and `to-spec` skills. Use this repo's own `tracer-slice` skill (`.claude/skills/tracer-slice/`) first on every slice — it plans the vertical cut and the deep module before handing off to those generic skills.

## Workflow

1. `gh issue view <n>` to read the slice.
2. Branch `slice/<n>-<slug>` off `main`.
3. Plan with `tracer-slice`: name the deep module(s), confirm the slice is genuinely vertical.
4. Tests first, then implementation, one seam at a time.
5. Verify: run the checks below.
6. Open a PR with `Closes #<n>`.

## Commands

- Root: `pnpm install` (also installs the Husky `pre-push` hook), `pnpm test` (Vitest, all projects), `pnpm typecheck`.
- `git push` runs `pnpm test`, plus `uv run pytest` once `apps/api/pyproject.toml` exists. Don't bypass it with `--no-verify` unless the user asks.

Placeholders — filled in by the walking-skeleton slice (#2):

- Backend: `uv run pytest`, `uv run ruff check .`, `uv run alembic upgrade head`
- Frontend: `pnpm lint`, `pnpm build`

## Design

UI in `apps/web` follows one direction, set by the `taste-skill` plugin (enabled in `.claude/settings.json`). See `docs/adr/0001-design-direction-taste-skill.md`.

- Before writing or changing any UI, invoke `taste-skill:design-taste-frontend`. It is the anti-slop gate and runs the pre-flight check.
- Direction: `taste-skill:minimalist-ui`. Warm monochrome, typographic contrast, flat bento grids, no gradients, no heavy shadows. Use one direction only; don't mix styles.
- Redesigns or audits of existing screens: `taste-skill:redesign-existing-projects`.
- Don't use `gpt-taste`, `industrial-brutalist-ui`, `image-to-code`, or the `imagegen-*` skills for app UI.
- taste-skill sits on top of shadcn/ui + Tailwind. It decides tokens, spacing, type and states; it doesn't replace the component library.

## Agent skills

### Issue tracker

Issues live as GitHub issues on `Lam-Thai/Tether`, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), not yet created as GitHub labels. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: `CONTEXT.md` + `docs/adr/` at the repo root, created lazily. See `docs/agents/domain.md`.
