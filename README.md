# Tether

AI receptionist for local service businesses. See `PRD.md` for the product and `CLAUDE.md` for engineering conventions.

## Getting started

Requirements:

- Node.js 24 LTS or newer (`.nvmrc` pins 24)
- pnpm 12 (`npm i -g pnpm@12`; the exact version is pinned in `package.json` → `packageManager`)
- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/) for `apps/api`
- Docker (for a local Postgres via `docker compose`), or a [Neon](https://neon.tech) dev branch

```sh
pnpm install              # installs JS/TS dependencies and the Husky git hooks
docker compose up --wait  # starts local Postgres and waits for its healthcheck (apps/api/.env's DATABASE_URL points here by default)
cp apps/api/.env.example apps/api/.env      # fill in DATABASE_URL etc.
cp apps/web/.env.example apps/web/.env.local
uv sync --directory apps/api               # installs Python dependencies
uv run --directory apps/api alembic upgrade head   # apply all migrations

pnpm dev       # runs apps/web (Next.js) and apps/api (uvicorn --reload) together
```

Open http://localhost:3000 for the landing page, and http://localhost:3000/status for `API: healthy · Database: connected`.

### Billing (Stripe test mode)

`apps/api` won't start without the Stripe settings (see `apps/api/.env.example`). Use **test-mode** keys only; a live key is rejected unless `ENVIRONMENT=production`.

1. In the Stripe Dashboard (test mode), copy the secret key into `STRIPE_SECRET_KEY`, and create a recurring monthly price; put its id in `STRIPE_PRICE_ID`.
2. Set `APP_BASE_URL=http://localhost:3000`.
3. Forward webhooks to the local API and copy the `whsec_...` it prints into `STRIPE_WEBHOOK_SECRET`:

   ```sh
   stripe login
   stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe
   ```

Pay with test card `4242 4242 4242 4242` (any future date, any CVC). The free trial needs no card.

### AI receptionist (Gemini)

The test chat at `/dashboard/agent` calls real Gemini through `apps/api`. Create a key in [Google AI Studio](https://aistudio.google.com/apikey) and set `GEMINI_API_KEY` in `apps/api/.env` (see `apps/api/.env.example` for the model and timeout settings). Without a key the API still starts locally, but every reply is the "having trouble responding" fallback. Keep `ENVIRONMENT=local` locally; `production` requires the key and is only for the deployed API.

Optional live smoke test against real Gemini:

```sh
uv run --directory apps/api pytest -m live
```

Other root commands:

```sh
pnpm test      # runs Vitest across all projects (tooling + apps/web)
pnpm typecheck # strict TypeScript check of root tooling
```

See the root `CLAUDE.md` for the full command reference (per-app lint/typecheck/test/build commands).

### Pre-push hook

`git push` runs the Husky `pre-push` hook (`.husky/pre-push`) and is rejected if any check fails:

1. `pnpm test`, the Vitest suite.
2. `uv run pytest` in `apps/api` (skipped only if `apps/api/pyproject.toml` is missing, which is no longer the normal case).

The suite includes `tooling/repo-hygiene.test.ts`, which fails if any env file other than `.env.example` is tracked (`.env*`, `.envrc`, `*.env`).

If a git GUI client reports `pnpm: command not found` from the hook, it isn't loading your shell's PATH (common with nvm/fnm). Add the PATH setup to `~/.config/husky/init.sh`, which Husky sources before every hook.

In a real emergency you can skip the hook with `git push --no-verify`. CI still has to pass before merge.

## Deployment

CI (`.github/workflows/api.yml`, `.github/workflows/web.yml`) lints, type-checks, tests, and builds both apps on every PR. Actually deploying is a one-time manual setup:

1. **Neon**: create a project, grab the pooled connection string (`postgresql://...`). You'll set this as `DATABASE_URL` in the API's Vercel project — the backend normalizes it to the `asyncpg` driver automatically.
2. **Vercel — API** (`apps/api`): new project, Root Directory = `apps/api`. Vercel auto-detects the FastAPI app from `main.py` (no `vercel.json` needed). Env vars: `DATABASE_URL` (from Neon), `CORS_ORIGINS` (the web app's deployed URL, once known), `ENVIRONMENT=production`, `APP_VERSION` (e.g. a git SHA or release tag).
3. **Vercel — Web** (`apps/web`): new project, Root Directory = `apps/web`. Env vars: `NEXT_PUBLIC_API_BASE_URL` (the API project's deployed URL from step 2).
4. Go back and update the API project's `CORS_ORIGINS` with the web app's actual deployed URL from step 3, then redeploy the API.
5. Run `uv run alembic upgrade head` against the Neon database once (locally, with `DATABASE_URL` pointed at Neon) before relying on it — migrations are never run automatically on startup or in CI against Neon.

Once both are deployed, the web URL should show `API: healthy · Database: connected`.
