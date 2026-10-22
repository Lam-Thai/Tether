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
- Every outbound provider call goes through `UsageGuard` (`services/usage.py`) and uses bounded retries (`core/retry.py` `retry_bounded`). Every new provider adapter **must** be metered: call it inside `usage.metered(kind, business_id=...)`, with `retry_bounded` wrapped *around* `metered` so each attempt is reserved and counted. On `LimitExceededError`, answer with a safe fallback and never retry.

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

- Root: `pnpm install` (also installs the Husky `pre-push` hook), `pnpm test` (Vitest, all projects — tooling + `apps/web`), `pnpm typecheck` (root tooling).
- `pnpm dev` runs both apps at once (`apps/web` on Next.js dev server + `apps/api` on `uvicorn --reload`, via `concurrently`). Requires local Postgres running first (see below).
- `git push` runs `pnpm test` and the API suite (`apps/api/scripts/test.sh`) side by side, about 40s. The API half needs the local Postgres running (`docker compose up -d`). Don't bypass it with `--no-verify` unless the user asks.

Backend (`apps/api`, run from inside that directory or via `uv run --directory apps/api`):

- `uv sync` — install dependencies into `apps/api/.venv`.
- `uv run uvicorn main:app --reload` — run the dev server.
- `sh scripts/test.sh` — tests, the safe way: CI-like settings (no `.env`, so no real keys), migrations, then `pytest -n auto --dist loadfile` against a local Postgres (`TEST_DATABASE_URL`, default the docker compose one). Extra args go to pytest. Plain `uv run pytest` works too, but `tests/conftest.py` refuses `ENVIRONMENT=production` and any non-local database host unless `TEST_DATABASE_ALLOW_REMOTE=1`. DB-touching tests fail (not skip) if Postgres is unreachable. Keep tests parallel-safe: unique ids per test, the `usage` fixture's own UTC day, stable parametrize ids.
- `uv run tether-eval --suite core [--tier free|paid]` — the AI eval harness against real Gemini (see `apps/api/CLAUDE.md`, "Eval harness"). Costs real requests; never part of `git push` or CI's PR checks.
- `uv run ruff check .` — lint.
- `uv run pyright` — type-check.
- `uv run alembic upgrade head` / `uv run alembic revision --autogenerate -m "..."` — migrations. Never run from app startup code.

Frontend (`apps/web`, via `pnpm --filter web <script>` from the root, or directly inside the directory):

- `pnpm --filter web dev` / `lint` / `typecheck` / `test` / `build`.

Local Postgres for `apps/api`: `docker compose up -d` at the repo root (see `docker-compose.yml`), then point `apps/api/.env`'s `DATABASE_URL` at `postgresql://tether:tether@127.0.0.1:5432/tether` (`127.0.0.1`, not `localhost`: on Windows `localhost` tries IPv6 first and every new connection waits ~2s). A Neon dev branch works too — same `DATABASE_URL`, just point it at Neon's pooled connection string instead.

## Design

All UI in `apps/web` (marketing and product) follows one design system: **Construtivismo Dinâmico Tech**, defined in `apps/web/DESIGN.md`. See `docs/adr/0002-design-system-construtivismo-dinamico.md`, which supersedes ADR 0001.

- Before writing or changing any UI, invoke `taste-skill:design-taste-frontend`. It is the anti-slop gate and runs the pre-flight check, but the direction comes from `DESIGN.md`, not from a taste-skill style.
- `DESIGN.md`'s **Resolved decisions** table overrides the spec text wherever they disagree (contrast fixes, no pure black, light mode only). Add a new visual need as a token or a decision there, never as a one-off hex value in a component.
- Redesigns or audits of existing screens: `taste-skill:redesign-existing-projects`, against `DESIGN.md`.
- Don't use `minimalist-ui`, `gpt-taste`, `industrial-brutalist-ui`, `image-to-code`, or the `imagegen-*` skills for app UI.
- The design system sits on top of shadcn/ui + Tailwind. It decides tokens, spacing, type and states; it doesn't replace the component library.
- `tooling/repo-hygiene.test.ts` fails CI on `h-screen`, pure black, gradients, or emoji in `apps/web/{app,components}`.

## Agent skills

### Issue tracker

Issues live as GitHub issues on `Lam-Thai/Tether`, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), not yet created as GitHub labels. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: `CONTEXT.md` + `docs/adr/` at the repo root, created lazily. See `docs/agents/domain.md`.
