---
name: api-endpoint
description: Add or change a FastAPI endpoint in apps/api. Use when the slice needs a new route, or an existing route's request/response shape or authorization changes.
---

# API endpoint

## Steps

1. **Schema first.** Define the Pydantic request and response models in `schemas` before writing the route.
2. **Router.** Add the route in `api`. It only parses the request, resolves `business_id` from `TenantContext` (never from the path or body), calls one service method, and maps the result to the response schema. No business logic here.
3. **Service.** Business logic lives in `services`, scoped to the resolved `business_id`. It calls `repositories` for data access and `integrations` for external calls — never SQLAlchemy or the Gemini/Twilio/Calendar clients directly.
4. **Repository.** Every query filtered by `business_id`. If the repository doesn't exist yet for this resource, this is where it's created.
5. **Error mapping.** Map service-level errors (not found, forbidden, validation) to the right HTTP status — a cross-tenant lookup should 404, not 403 (don't reveal that the resource exists for another tenant).
6. **Rate limit and isolation sweep (slice 17).** Every POST/PUT/PATCH/DELETE needs a rate-limit dependency: owner routers get `limit_tenant_mutations` from `api/v1/router.py`; a route that spends Gemini/TTS gets its own stricter rule (`core/rate_limit.py`); anything else is a reasoned `RATE_LIMIT_EXEMPT` entry (`tests/api/test_rate_limit.py` fails otherwise). The tenant-isolation sweep (`tests/security/isolation.py`) covers the route automatically, but a new path parameter needs a `RESOLVERS` entry, a required body or query a `CASES` entry, and a public route an `ALLOWLIST` reason; a seeded resource type the route reads goes into `seed_tenant`.
7. **Tests.** Cover the happy path, the error-mapping cases, and a cross-tenant test: business A's token cannot read or write business B's resource through this route, even by ID.

## Checklist

- [ ] Request/response schemas defined before the route.
- [ ] `business_id` resolved from `TenantContext`, not from client input.
- [ ] Router has no business logic; service has no FastAPI/SQLAlchemy imports.
- [ ] Cross-tenant access returns 404, not 403.
- [ ] Cross-tenant test included.
- [ ] Mutating route rate-limited; the isolation sweep passes with no new allowlist entry unless justified.
