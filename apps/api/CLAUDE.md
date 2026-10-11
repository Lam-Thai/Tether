# apps/api

FastAPI backend. See the root `CLAUDE.md` for the hard rules and the layer map (`api / core / schemas / services / repositories / models / integrations / tests`) — this file only adds conventions specific to this app.

Stub: filled in by the walking-skeleton slice (#2) once the app exists. Expect sections on:

- Error model (exception hierarchy → HTTP status mapping).
- The repository pattern and how every query is scoped by `business_id`.
- `UsageGuard` and `TenantContext` — where they live, how a route pulls them in.
- Pydantic schema conventions (request vs. response models, shared base classes).
- How integrations (`Gemini`, `Twilio`, `Google Calendar`) are faked in tests.
