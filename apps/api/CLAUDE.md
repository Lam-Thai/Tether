# apps/api

FastAPI backend. See the root `CLAUDE.md` for the hard rules and the layer map (`api / core / schemas / services / repositories / models / integrations / tests`) — this file only adds conventions specific to this app.

## Layout

```
main.py              # entrypoint: `from core.app import create_app; app = create_app()`
core/                 # settings, db engine/session, app factory, error envelope + handlers
schemas/              # Pydantic request/response models
services/             # business logic (deep modules) — no FastAPI or SQLAlchemy imports
repositories/         # the only layer that queries the DB
models/               # SQLAlchemy ORM models + shared declarative Base
integrations/         # external provider clients, each behind a port + in-memory fake (clerk/ so far)
api/                  # routers; `api/health.py` mounted at app root, `api/v1/` for everything else
migrations/           # async Alembic environment
tests/                # mirrors the layers above
```

## Settings

`core.config.Settings` is the *only* place that reads environment variables. Every other module imports the `settings` singleton from `core.config` instead of touching `os.environ`. Required: `DATABASE_URL`, `CLERK_ISSUER` (e.g. `https://<slug>.clerk.accounts.dev`; `clerk_jwks_url` is derived from it, never set separately). Required for billing: `STRIPE_SECRET_KEY` (test-mode `sk_test_`/`rk_test_` unless `ENVIRONMENT=production`; a live key elsewhere fails at startup), `STRIPE_WEBHOOK_SECRET` (`whsec_`, from `stripe listen` locally or the dashboard endpoint in deploys), `STRIPE_PRICE_ID`, `APP_BASE_URL` (the web origin Checkout and the Portal return to). Optional: `BILLING_TRIAL_DAYS` (default 14), `CORS_ORIGINS` (JSON array or comma-separated string), `CLERK_AUTHORIZED_PARTIES` (JSON array or comma-separated string of allowed `azp` origins — empty skips the check, for local dev), `ENVIRONMENT`, `APP_VERSION`. The app never reads `CLERK_SECRET_KEY`: RS256 verification only needs Clerk's public JWKS, so the backend has no Clerk secret at all.

## Auth and tenancy

- `core/auth.py`: `verify_principal(token, resolve_signing_key, issuer, authorized_parties) -> Principal` is the only function that knows a Clerk token's claim shape (`sub`, `email`, `azp`). Every failure — bad signature, expired, wrong issuer, unrecognized `azp`, unreachable JWKS — maps to the same `AuthError` (401). `resolve_signing_key` is the `integrations/clerk` port; tests pass an in-memory fake backed by a real RSA keypair instead of a live JWKS fetch.
- `core/tenant.py`: `get_tenant` is the `TenantContext` FastAPI dependency — `get_tenant() -> Tenant(user, business)`. It verifies the bearer token, just-in-time upserts the `users` row from the Clerk subject, and resolves `business` from `owner_id` only. `business` is `None` before onboarding. Every business-scoped router depends on `Tenant`, never on a raw `business_id` from the client.
- `repositories/base.py`: the `BusinessScopedRepository` pattern from issue #3, implemented as a function (`business_scoped_select(model, business_id)`) rather than a class, to match this repo's function-style repositories (see `repositories/health.py`). Every domain repository with a `business_id` column builds its queries on this, always called with `tenant.business.id` — never a client-supplied id.
- Services stay free of SQLAlchemy imports by taking repository functions pre-bound to the session via `functools.partial` (see `services/business.py` / `api/v1/business.py`), the same pattern `services/health.py` already uses for `check_db`.

## DB sessions

`core.db` builds one async SQLAlchemy engine per process (`NullPool`, `pool_pre_ping=True`, 5s statement timeout — serverless-safe, since Neon's own pooler handles connection pooling at the proxy layer) and exposes `get_session()` as a FastAPI dependency that yields one session per request. A plain `postgresql://` URL (what Neon's UI gives you) is rewritten to `postgresql+asyncpg://` automatically.

## Error model

Every error response is the same envelope: `{"error": {"code": "<snake_case_code>", "message": "<human message>"}}`. `core/errors.py` defines `AppError` (status_code + code class attrs) as the base for domain errors, and registers handlers for `AppError`, Starlette's `HTTPException`, `RequestValidationError` (422, `validation_error`), and a catch-all (500, `internal_error` — the real exception is logged server-side via `logging`, never leaked to the client). Add new domain error types by subclassing `AppError`; don't invent a new envelope shape.

## Repository pattern

`repositories/` is the only layer that queries the DB. The health check (`repositories/health.py`) is a connectivity probe with no tenant data, so it doesn't take a `business_id`. `repositories/users.py` and `repositories/business.py` are the two exceptions that establish identity before a `Tenant` exists (scoped by `external_auth_id` / `owner_id`); every later domain table with its own `business_id` column scopes through `repositories/base.py` instead (see "Auth and tenancy" above).

## Routing convention

Liveness/infra checks (`/health`) mount unversioned at the app root — they're not business endpoints. Everything else mounts under `/api/v1` (see `api/v1/router.py`, currently empty). Routers only parse the request, call one service method, and shape the response — no business logic or direct DB access in `api/`.

## Alembic

Async environment (`migrations/env.py`) reads `settings.database_url` through the same normalization `core.db` uses — never a hardcoded URL in `alembic.ini`. Workflow: change a SQLAlchemy model → `uv run alembic revision --autogenerate -m "..."` → hand-review the generated SQL (indexes on `business_id`, FKs, unique constraints, timestamps) → write and test the `downgrade()`. Never call `alembic upgrade head` from application startup code; it's a separate, explicit command.

## Billing and entitlement

- `services/billing.py` is the deep module (`start_checkout`, `sync_checkout`, `apply_event`, `open_portal`, `get_plan`, `summarize`, `access_for`). Stripe sits behind `integrations/stripe/port.py` (`BillingGateway`) with a real adapter (`adapter.py`) and `FakeBillingGateway` (`fake.py`); no Stripe SDK type leaves that package.
- **Every business-feature router (Slice 4 onwards) depends on `core.billing.get_entitled_tenant`, not `get_tenant`.** It returns 402 `subscription_required` unless the business is `trialing`, `active` or `past_due`. Only onboarding, `/me`, `/business` and `/billing/*` stay on `get_tenant`.
- Stripe is the source of truth. The webhook (`POST /api/v1/webhooks/stripe`, raw-body signature check, `stripe_events` dedupe) and the return-page sync both **re-fetch** the subscription from Stripe instead of trusting payloads, under a per-business row lock (`repositories/billing.py`).
- Local webhooks: `stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe`, then put the printed `whsec_...` in `STRIPE_WEBHOOK_SECRET`. Opt-in live smoke test: `STRIPE_LIVE_TESTS=1 uv run pytest -m live`.
- No card data ever reaches Tether: Checkout and the Billing Portal are Stripe-hosted, and the web app holds no Stripe key at all.

## Still placeholders (land with the slice that needs them)

- `UsageGuard` — not introduced yet (#5). Until then Stripe calls rely on bounded SDK retries (`max_network_retries=2`), idempotency keys and a 15s per-call timeout; #5 must wrap `BillingGateway` too. `TenantContext` landed in slice 3 (`core/tenant.py`).
- `integrations/clerk/` is the first provider client, behind `integrations/clerk/port.py` with a real JWKS adapter (`resolver.py`) and an in-memory fake (`fake.py`), per the root `CLAUDE.md`. `integrations/stripe/` (#28) follows the same shape. Gemini/Twilio/Google Calendar still need their own.

## Testing

`uv run pytest` from inside `apps/api/`. Tests mirror the layers: unit tests for `services/` use fake callables (no DB); `repositories/` and endpoint-level `api/` tests that need a real Postgres read `DATABASE_URL` from the environment and are intentionally not skipped when it's unreachable — they should fail loudly, not pass silently.
