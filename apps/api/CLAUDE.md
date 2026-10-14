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
integrations/         # external provider clients, each behind a port + in-memory fake (empty so far)
api/                  # routers; `api/health.py` mounted at app root, `api/v1/` for everything else
migrations/           # async Alembic environment
tests/                # mirrors the layers above
```

## Settings

`core.config.Settings` is the *only* place that reads environment variables. Every other module imports the `settings` singleton from `core.config` instead of touching `os.environ`. Required: `DATABASE_URL`. Optional: `CORS_ORIGINS` (JSON array or comma-separated string), `ENVIRONMENT`, `APP_VERSION`.

## DB sessions

`core.db` builds one async SQLAlchemy engine per process (`NullPool`, `pool_pre_ping=True`, 5s statement timeout — serverless-safe, since Neon's own pooler handles connection pooling at the proxy layer) and exposes `get_session()` as a FastAPI dependency that yields one session per request. A plain `postgresql://` URL (what Neon's UI gives you) is rewritten to `postgresql+asyncpg://` automatically.

## Error model

Every error response is the same envelope: `{"error": {"code": "<snake_case_code>", "message": "<human message>"}}`. `core/errors.py` defines `AppError` (status_code + code class attrs) as the base for domain errors, and registers handlers for `AppError`, Starlette's `HTTPException`, `RequestValidationError` (422, `validation_error`), and a catch-all (500, `internal_error` — the real exception is logged server-side via `logging`, never leaked to the client). Add new domain error types by subclassing `AppError`; don't invent a new envelope shape.

## Repository pattern

`repositories/` is the only layer that queries the DB. The health check (`repositories/health.py`) is a connectivity probe with no tenant data, so it doesn't take a `business_id` — once the first tenant-scoped table lands, every repository query must be scoped by `business_id` resolved from `TenantContext` (not yet introduced in this slice; land it alongside the first business-data slice instead of inventing it ahead of need).

## Routing convention

Liveness/infra checks (`/health`) mount unversioned at the app root — they're not business endpoints. Everything else mounts under `/api/v1` (see `api/v1/router.py`, currently empty). Routers only parse the request, call one service method, and shape the response — no business logic or direct DB access in `api/`.

## Alembic

Async environment (`migrations/env.py`) reads `settings.database_url` through the same normalization `core.db` uses — never a hardcoded URL in `alembic.ini`. Workflow: change a SQLAlchemy model → `uv run alembic revision --autogenerate -m "..."` → hand-review the generated SQL (indexes on `business_id`, FKs, unique constraints, timestamps) → write and test the `downgrade()`. Never call `alembic upgrade head` from application startup code; it's a separate, explicit command.

## Still placeholders (land with the slice that needs them)

- `UsageGuard` / `TenantContext` — not introduced yet; no outbound provider calls or tenant-scoped data exist in this slice.
- `integrations/` — empty package; the first real provider client (Gemini/Twilio/Google Calendar) must ship behind a small interface with an in-memory fake, per the root `CLAUDE.md`.

## Testing

`uv run pytest` from inside `apps/api/`. Tests mirror the layers: unit tests for `services/` use fake callables (no DB); `repositories/` and endpoint-level `api/` tests that need a real Postgres read `DATABASE_URL` from the environment and are intentionally not skipped when it's unreachable — they should fail loudly, not pass silently.
