"""The automatic tenant-isolation sweep.

Every route of the app is either SWEPT (tenant B calls it against tenant A's resources) or
listed in `ALLOWLIST` with a reason. A route in neither place is a finding, so a new endpoint
cannot ship uncovered: the author has to teach the sweep about it (a `CASES` entry, a
`RESOLVERS` entry for a new path parameter) or justify the opt-out.

Interface:

    discover_routes(app) -> list[RouteSpec]        # OpenAPI routes AND hidden ones
    seed_tenant(client, tenant, ...) -> SeededTenant   # one of every resource, canaries planted
    await sweep(client, app, owner, intruder, ...) -> list[Finding]   # never asserts

`sweep` returns findings instead of raising so a meta-test can prove it DETECTS a leak
(`test_isolation_sweep_detects_leaks.py`).

What is checked per swept route, acting as the intruder with the owner's ids:
  * without a token: 401;
  * with the intruder's token: the expected status (403/404 for a route that names an owner id);
  * no response body contains anything the owner owns (ids, canaries, email, phone, ...),
    except values the request itself supplied;
  * the owner's own GET responses are identical before and after the whole sweep.
"""

import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from httpx import AsyncClient

from core.db import async_session_factory
from integrations.google_calendar.fake import fake_calendar
from integrations.llm.fake import FakeLLMClient, agent_turn
from integrations.stripe.fake import FakeBillingGateway
from integrations.twilio.fake import random_number_sid, random_phone_number
from models.booking import Booking
from models.escalation import Escalation
from models.phone_number import PhoneNumber
from tests.conftest import CalendarHarness, TenantFixture

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
RouteKey = tuple[str, str]

# ---------------------------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteSpec:
    method: str
    path: str
    path_params: tuple[str, ...]
    has_body: bool
    body_required: bool
    required_query: tuple[str, ...]
    auth: bool  # the operation takes the bearer `Authorization` header (a tenant route)
    in_schema: bool

    @property
    def key(self) -> RouteKey:
        return (self.method, self.path)


def _iter_route_objects(routes: Any) -> Iterator[Any]:
    """Every concrete route, flattening FastAPI's lazily included routers."""
    for route in routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if contexts is not None:
            yield from contexts()
        elif hasattr(route, "path") and hasattr(route, "methods"):
            yield route


def discover_routes(app: FastAPI) -> list[RouteSpec]:
    """All routes: those in the OpenAPI schema (with body/query/auth details) plus the ones
    with `include_in_schema=False` (webhooks, media, docs), found by walking the app's routes."""
    openapi = app.openapi()
    specs: dict[RouteKey, RouteSpec] = {}
    for path, operations in openapi["paths"].items():
        for method, operation in operations.items():
            if method.upper() not in METHODS:
                continue
            parameters = operation.get("parameters", [])
            body = operation.get("requestBody")
            specs[(method.upper(), path)] = RouteSpec(
                method=method.upper(),
                path=path,
                path_params=tuple(re.findall(r"{(\w+)}", path)),
                has_body=body is not None,
                body_required=bool(body and body.get("required")),
                required_query=tuple(
                    p["name"] for p in parameters if p["in"] == "query" and p.get("required")
                ),
                auth=any(
                    p["in"] == "header" and p["name"].lower() == "authorization" for p in parameters
                ),
                in_schema=True,
            )
    for route in _iter_route_objects(app.routes):
        for method in sorted(set(route.methods or ()) & set(METHODS)):
            key = (method, route.path)
            if key not in specs:
                specs[key] = RouteSpec(
                    method=method,
                    path=route.path,
                    path_params=tuple(re.findall(r"{(\w+)}", route.path)),
                    has_body=False,
                    body_required=False,
                    required_query=(),
                    auth=False,
                    in_schema=False,
                )
    return sorted(specs.values(), key=lambda s: (s.path, s.method))


# ---------------------------------------------------------------------------------------------
# Classification: what is NOT swept, and why
# ---------------------------------------------------------------------------------------------

WEBHOOK_REASON = (
    "Provider webhook, authenticated by signature, not a bearer token; covered by the "
    "signature sweep in test_webhook_signatures.py, which discovers it from the route table."
)
PUBLIC_INFRA = "Public infrastructure/docs route; reads and returns no tenant data."

ALLOWLIST: dict[RouteKey, str] = {
    ("GET", "/health"): "Liveness probe; returns only service/database status, no tenant data.",
    ("GET", "/openapi.json"): PUBLIC_INFRA,
    ("GET", "/docs"): PUBLIC_INFRA,
    ("GET", "/docs/oauth2-redirect"): PUBLIC_INFRA,
    ("GET", "/redoc"): PUBLIC_INFRA,
    ("POST", "/api/v1/webhooks/stripe"): WEBHOOK_REASON,
    ("POST", "/api/v1/webhooks/twilio/sms-status"): WEBHOOK_REASON,
    ("POST", "/api/v1/webhooks/twilio/voice"): WEBHOOK_REASON,
    ("POST", "/api/v1/webhooks/twilio/input"): WEBHOOK_REASON,
    ("POST", "/api/v1/webhooks/twilio/input/wait"): WEBHOOK_REASON,
    ("POST", "/api/v1/webhooks/twilio/status"): WEBHOOK_REASON,
    ("GET", "/api/v1/media/{media_token}"): (
        "Public capability URL: the unguessable, expiring token IS the credential and no "
        "tenant id is accepted; misses are covered by tests/api/test_media.py."
    ),
}


def webhook_routes(app: FastAPI) -> list[RouteSpec]:
    """The routes the signature sweep must cover: everything the allowlist files under
    `WEBHOOK_REASON`, plus anything under the webhooks prefixes (so a new webhook shows up)."""
    return [
        spec
        for spec in discover_routes(app)
        if ALLOWLIST.get(spec.key) == WEBHOOK_REASON or spec.path.startswith("/api/v1/webhooks/")
    ]


# ---------------------------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------------------------


def canary(label: str) -> str:
    """A unique, letters-only marker (digits would trip the grounding/redaction guards when it
    is spoken in a chat reply)."""
    letters = uuid.uuid4().hex.translate(str.maketrans("0123456789", "ghijklmnop"))
    return f"canary{label}{letters[:24]}"


@dataclass
class SeededTenant:
    tenant: TenantFixture
    ids: dict[str, str] = field(default_factory=dict)
    canaries: list[str] = field(default_factory=list)
    extra_secrets: list[str] = field(default_factory=list)
    oauth_state: str = ""

    @property
    def token(self) -> str:
        return self.tenant.token

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tenant.token}"}

    @property
    def secrets(self) -> set[str]:
        """Everything of this tenant's that must never appear in another tenant's response."""
        return {
            str(self.tenant.business_id),
            self.tenant.email,
            self.tenant.external_auth_id,
            *self.ids.values(),
            *self.canaries,
            *self.extra_secrets,
        }


async def seed_tenant(
    client: AsyncClient,
    tenant: TenantFixture,
    *,
    llm: FakeLLMClient,
    billing_gateway: FakeBillingGateway,
    calendar: CalendarHarness,
) -> SeededTenant:
    """Creates one of EVERY resource type for `tenant` (through the real API wherever one
    exists), planting canaries in its text fields. The tenant must be entitled."""
    seeded = SeededTenant(tenant=tenant)
    headers = seeded.headers

    def plant(label: str) -> str:
        value = canary(label)
        seeded.canaries.append(value)
        return value

    async def call(method: str, url: str, expected: int, **kwargs: Any) -> Any:
        response = await client.request(method, url, headers=headers, **kwargs)
        assert response.status_code == expected, f"seed {method} {url}: {response.text}"
        return response.json() if response.content else None

    seeded.ids["business_id"] = str(tenant.business_id)
    seeded.extra_secrets.append(tenant.email)

    await call(
        "PATCH",
        f"/api/v1/business/{tenant.business_id}",
        200,
        json={
            "name": plant("name"),
            "address": plant("address"),
            "contact_email": f"{plant('mail')}@example.com",
        },
    )
    await call(
        "PATCH",
        "/api/v1/agent",
        200,
        json={"greeting": plant("greeting"), "custom_instructions": plant("instructions")},
    )
    faq = await call(
        "POST", "/api/v1/faqs", 201, json={"question": plant("faqq"), "answer": plant("faqa")}
    )
    seeded.ids["faq_id"] = faq["id"]
    service = await call(
        "POST",
        "/api/v1/services",
        201,
        json={
            "name": plant("service"),
            "description": plant("servicedesc"),
            "duration_minutes": 45,
            "price": "40",
        },
    )
    seeded.ids["service_id"] = service["id"]
    week = [
        {"day_of_week": d, "is_closed": True}
        if d in (5, 6)
        else {"day_of_week": d, "is_closed": False, "opens_at": "09:00", "closes_at": "17:00"}
        for d in range(7)
    ]
    await call("PUT", "/api/v1/business/hours", 200, json={"days": week})
    await call(
        "PUT",
        "/api/v1/business/booking-rules",
        200,
        json={
            "slot_interval_minutes": 30,
            "min_notice_minutes": 60,
            "max_days_ahead": 30,
            "buffer_minutes": 10,
        },
    )
    await call(
        "PUT",
        "/api/v1/business/escalation-settings",
        200,
        json={
            "contact_phone": random_phone_number(),
            "sms_enabled": False,
            "clarification_threshold": 3,
            "urgent_keywords": [plant("kw").lower()],
        },
    )

    # A test-chat interaction (the fake model replies with a canary).
    llm.queue(agent_turn(f"Noted {plant('reply')}."))
    chat = await call("POST", "/api/v1/agent/test", 200, json={"message": plant("message")})
    seeded.ids["interaction_id"] = chat["interaction_id"]

    # No API creates these: insert them like the existing API tests do.
    escalation_id = uuid.uuid4()
    booking_id = uuid.uuid4()
    number_id = uuid.uuid4()
    number = random_phone_number()
    sid = random_number_sid()
    start = datetime(2031, 3, 5, 15, 0, tzinfo=UTC)
    async with async_session_factory() as session:
        session.add(
            Escalation(
                id=escalation_id,
                business_id=tenant.business_id,
                interaction_id=uuid.UUID(chat["interaction_id"]),
                reason="complaint",
                urgency="normal",
                status="open",
                sms_status="disabled",
            )
        )
        session.add(
            PhoneNumber(
                id=number_id,
                business_id=tenant.business_id,
                provider="twilio",
                phone_number=number,
                provider_number_sid=sid,
                status="connected",
            )
        )
        session.add(
            Booking(
                id=booking_id,
                business_id=tenant.business_id,
                interaction_id=uuid.UUID(chat["interaction_id"]),
                service_id=uuid.UUID(service["id"]),
                service_name=service["name"],
                timezone="America/New_York",
                calendar_event_id=f"evt{booking_id.hex}",
                customer_name=plant("customer"),
                customer_phone=random_phone_number(),
                start_time=start,
                end_time=start + timedelta(minutes=45),
                blocked_until=start + timedelta(minutes=55),
                status="confirmed",
                idempotency_key=f"seed:{booking_id.hex}",
            )
        )
        await session.commit()
    seeded.ids.update(
        escalation_id=str(escalation_id), booking_id=str(booking_id), number_id=str(number_id)
    )
    seeded.ids.update(provider_number_sid=sid, phone_number=number)

    # Billing: the checkout session this tenant's fixture created through the fake gateway.
    customer = billing_gateway.customers.get(tenant.business_id)
    for session_id, info in billing_gateway.sessions.items():
        if customer is not None and info.customer_id == customer:
            seeded.ids["checkout_session_id"] = session_id
            seeded.extra_secrets.append(customer)
            break

    # Google Calendar: connected with a canary-named calendar.
    work = fake_calendar(f"{plant('calendar')}@example.com", plant("calendarname"), primary=True)
    calendar.api.issue_grant(calendars=[work])
    connect = await call("GET", "/api/v1/calendar/connect", 200)
    seeded.oauth_state = parse_qs(urlparse(connect["url"]).query)["state"][0]
    await call(
        "POST",
        "/api/v1/calendar/callback",
        200,
        json={"code": f"code-{uuid.uuid4().hex}", "state": seeded.oauth_state},
    )
    await call("PATCH", "/api/v1/calendar", 200, json={"calendar_id": work.id})
    seeded.ids["calendar_id"] = work.id
    return seeded


# ---------------------------------------------------------------------------------------------
# What the sweep sends
# ---------------------------------------------------------------------------------------------

Payload = Callable[[SeededTenant], Any]


@dataclass(frozen=True)
class Case:
    """How to call one route. `body` / `query` receive the OWNER (the tenant under attack), so
    a body or query that names an owner resource uses the owner's ids. `expect` is the set of
    acceptable statuses; the default is 403/404 for a route with a path parameter and "any
    non-5xx answer that proves the request was exercised" otherwise. `last` orders routes that
    destroy the intruder's own tenant (higher runs later)."""

    body: Payload | None = None
    query: Payload | None = None
    expect: frozenset[int] | None = None
    last: int = 0


def _missing(_: SeededTenant) -> None:  # pragma: no cover - placeholder never called
    return None


DELETE_ACCOUNT = "DELETE MY ACCOUNT"
NOT_FOUND = frozenset({403, 404})

CASES: dict[RouteKey, Case] = {
    ("GET", "/api/v1/me"): Case(),
    ("DELETE", "/api/v1/account"): Case(
        body=lambda _: {"confirmation": DELETE_ACCOUNT}, expect=frozenset({200}), last=3
    ),
    ("GET", "/api/v1/business/hours"): Case(),
    ("PUT", "/api/v1/business/hours"): Case(
        body=lambda _: {
            "days": [
                {"day_of_week": d, "is_closed": False, "opens_at": "08:00", "closes_at": "12:00"}
                for d in range(7)
            ]
        },
        expect=frozenset({200}),
    ),
    ("GET", "/api/v1/business/booking-rules"): Case(),
    ("PUT", "/api/v1/business/booking-rules"): Case(
        body=lambda _: {
            "slot_interval_minutes": 15,
            "min_notice_minutes": 30,
            "max_days_ahead": 14,
            "buffer_minutes": 5,
        },
        expect=frozenset({200}),
    ),
    ("GET", "/api/v1/business/escalation-settings"): Case(),
    ("PUT", "/api/v1/business/escalation-settings"): Case(
        body=lambda _: {
            "contact_phone": "+15550001111",
            "sms_enabled": False,
            "clarification_threshold": 2,
            "urgent_keywords": ["fire"],
        },
        expect=frozenset({200}),
    ),
    # The owner's interaction as a QUERY filter: the intruder must see nothing.
    ("GET", "/api/v1/escalations"): Case(
        query=lambda o: {"status": "all", "interaction_id": o.ids["interaction_id"]},
        expect=frozenset({200, 404}),
    ),
    ("PATCH", "/api/v1/escalations/{escalation_id}"): Case(body=lambda _: {"status": "resolved"}),
    ("GET", "/api/v1/dashboard/summary"): Case(),
    ("GET", "/api/v1/integrations/status"): Case(),
    ("DELETE", "/api/v1/business/test-data"): Case(
        body=lambda _: {"confirmation": "DELETE TEST DATA"}, last=1
    ),
    ("GET", "/api/v1/business"): Case(),
    # The intruder already has a business: any non-5xx answer, never the owner's.
    ("POST", "/api/v1/business"): Case(
        body=lambda _: {
            "name": "Intruder Co",
            "industry": "Salon",
            "address": "1 Test St",
            "timezone": "America/New_York",
        }
    ),
    ("PATCH", "/api/v1/business/{business_id}"): Case(body=lambda _: {"name": "Hijacked"}),
    ("GET", "/api/v1/billing/plan"): Case(),
    ("GET", "/api/v1/billing/subscription"): Case(),
    ("POST", "/api/v1/billing/checkout"): Case(body=lambda _: {"choice": "trial"}),
    ("POST", "/api/v1/billing/checkout/{session_id}/sync"): Case(),
    ("POST", "/api/v1/billing/portal"): Case(),
    ("GET", "/api/v1/agent"): Case(),
    ("PATCH", "/api/v1/agent"): Case(body=lambda _: {"name": "Intruder Agent"}),
    ("GET", "/api/v1/agent/readiness"): Case(),
    ("POST", "/api/v1/agent/activate"): Case(),
    ("POST", "/api/v1/agent/deactivate"): Case(last=1),
    ("GET", "/api/v1/agent/voices"): Case(),
    # TTS is unconfigured in tests: the 503 is the handler running and refusing, owner-free.
    ("POST", "/api/v1/agent/voice-preview"): Case(
        body=lambda _: {"voice": "Kore"}, expect=frozenset({200, 503})
    ),
    # Continues the OWNER's conversation: must be a miss, not a reply.
    ("POST", "/api/v1/agent/test"): Case(
        body=lambda o: {"interaction_id": o.ids["interaction_id"], "message": "Hello?"},
        expect=NOT_FOUND,
    ),
    ("GET", "/api/v1/agent/test/{interaction_id}"): Case(),
    ("POST", "/api/v1/agent/test/{interaction_id}/end"): Case(),
    ("GET", "/api/v1/faqs"): Case(),
    ("POST", "/api/v1/faqs"): Case(body=lambda _: {"question": "Own question?", "answer": "Yes."}),
    ("PATCH", "/api/v1/faqs/{faq_id}"): Case(body=lambda _: {"answer": "Hijacked answer"}),
    ("DELETE", "/api/v1/faqs/{faq_id}"): Case(),
    ("GET", "/api/v1/services"): Case(),
    ("POST", "/api/v1/services"): Case(
        body=lambda _: {"name": "Own service", "duration_minutes": 30}
    ),
    ("PATCH", "/api/v1/services/{service_id}"): Case(body=lambda _: {"name": "Hijacked service"}),
    ("DELETE", "/api/v1/services/{service_id}"): Case(),
    ("GET", "/api/v1/usage"): Case(),
    ("GET", "/api/v1/calendar/connect"): Case(),
    # A state minted for the OWNER, replayed by the intruder: login CSRF, must be a 400.
    ("POST", "/api/v1/calendar/callback"): Case(
        body=lambda o: {"code": "code-from-intruder", "state": o.oauth_state},
        expect=frozenset({400}),
    ),
    ("GET", "/api/v1/calendar/status"): Case(),
    ("GET", "/api/v1/calendar/calendars"): Case(),
    ("PATCH", "/api/v1/calendar"): Case(
        body=lambda o: {"calendar_id": o.ids["calendar_id"]},
        expect=frozenset({404, 409}),
    ),
    ("DELETE", "/api/v1/calendar"): Case(last=1),
    # The owner's service id in a query: another business's service is a 404.
    ("GET", "/api/v1/calendar/availability"): Case(
        query=lambda o: {"service_id": o.ids["service_id"], "date": "2031-03-05"},
        expect=NOT_FOUND,
    ),
    # The owner's conversation id in a query: the intruder lists nothing.
    ("GET", "/api/v1/bookings"): Case(
        query=lambda o: {"interaction_id": o.ids["interaction_id"]},
        expect=frozenset({200, 404}),
    ),
    ("GET", "/api/v1/interactions"): Case(),
    ("GET", "/api/v1/interactions/{interaction_id}"): Case(),
    ("GET", "/api/v1/interactions/{interaction_id}/messages"): Case(),
    ("GET", "/api/v1/phone-numbers"): Case(),
    # Claiming the owner's number: refused (in use or not found), never granted.
    ("POST", "/api/v1/phone-numbers"): Case(
        body=lambda o: {"provider_number_sid": o.ids["provider_number_sid"]},
        expect=frozenset({404, 409}),
    ),
    ("POST", "/api/v1/phone-numbers/{number_id}/test"): Case(),
    ("DELETE", "/api/v1/phone-numbers/{number_id}"): Case(),
}

# Path parameter name -> the owner's value for it.
RESOLVERS: dict[str, Payload] = {
    "business_id": lambda o: o.ids["business_id"],
    "faq_id": lambda o: o.ids["faq_id"],
    "service_id": lambda o: o.ids["service_id"],
    "interaction_id": lambda o: o.ids["interaction_id"],
    "escalation_id": lambda o: o.ids["escalation_id"],
    "number_id": lambda o: o.ids["number_id"],
    "session_id": lambda o: o.ids["checkout_session_id"],
}

# GETs whose payload legitimately changes between two calls (add with a reason).
SNAPSHOT_VOLATILE: dict[RouteKey, str] = {
    ("GET", "/api/v1/calendar/connect"): "Mints a fresh OAuth state and PKCE challenge per call.",
}

# ---------------------------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    kind: str
    method: str
    path: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.method} {self.path}: {self.detail}"


@dataclass
class _Prepared:
    spec: RouteSpec
    url: str
    params: dict[str, Any] | None
    body: Any
    supplied: set[str]  # values the request itself carries (reflecting them is not a leak)


def _leaks(text: str, owner: SeededTenant, supplied: set[str]) -> list[str]:
    return sorted(s for s in owner.secrets - supplied if s and s in text)


def _prepare(
    spec: RouteSpec,
    owner: SeededTenant,
    cases: dict[RouteKey, Case],
    resolvers: dict[str, Payload],
    findings: list[Finding],
) -> _Prepared | None:
    case = cases.get(spec.key, Case())
    url = spec.path
    supplied: set[str] = set()
    ok = True
    for name in spec.path_params:
        resolver = resolvers.get(name)
        if resolver is None:
            findings.append(
                Finding(
                    "unknown-param",
                    *spec.key,
                    f"teach the sweep about {{{name}}}: add a "
                    "RESOLVERS entry returning the owner's value",
                )
            )
            ok = False
            continue
        value = str(resolver(owner))
        supplied.add(value)
        url = url.replace("{" + name + "}", value)
    params = case.query(owner) if case.query else None
    missing_query = [q for q in spec.required_query if q not in (params or {})]
    if missing_query:
        findings.append(
            Finding(
                "no-query-case",
                *spec.key,
                f"required query {missing_query} has no CASES entry with a `query` callable",
            )
        )
        ok = False
    body = case.body(owner) if case.body else None
    if spec.body_required and body is None:
        findings.append(
            Finding(
                "no-body-case",
                *spec.key,
                "the route requires a request body but CASES has "
                "no `body` for it; add a valid example payload",
            )
        )
        ok = False
    supplied.update(str(v) for v in (params or {}).values())
    if isinstance(body, dict):
        supplied.update(str(v) for v in body.values())
    return _Prepared(spec, url, params, body, supplied) if ok else None


def _expected(spec: RouteSpec, case: Case, status: int) -> bool:
    if case.expect is not None:
        return status in case.expect
    if spec.path_params:
        return status in NOT_FOUND
    # Not naming an owner resource: it only has to prove it ran (no 5xx, no auth/plan/validation
    # failure that would mean the sweep never reached the handler).
    return status < 500 and status not in (401, 402, 405, 422)


async def _send(client: AsyncClient, prepared: _Prepared, headers: dict[str, str] | None) -> Any:
    return await client.request(
        prepared.spec.method,
        prepared.url,
        params=prepared.params,
        json=prepared.body,
        headers=headers,
    )


async def _snapshot(
    client: AsyncClient,
    owner: SeededTenant,
    specs: list[RouteSpec],
    cases: dict[RouteKey, Case],
    resolvers: dict[str, Payload],
) -> dict[str, Any]:
    """The owner's own view of every GET route (its own ids and queries)."""
    scratch: list[Finding] = []
    result: dict[str, Any] = {}
    for spec in specs:
        if spec.method != "GET" or spec.key in SNAPSHOT_VOLATILE:
            continue
        prepared = _prepare(spec, owner, cases, resolvers, scratch)
        if prepared is None:
            continue
        response = await _send(client, prepared, owner.headers)
        try:
            payload: Any = response.json()
        except ValueError:
            payload = response.text
        result[f"{spec.method} {prepared.url}"] = (response.status_code, payload)
    return result


async def sweep(
    client: AsyncClient,
    app: FastAPI,
    owner: SeededTenant,
    intruder: SeededTenant,
    *,
    cases: dict[RouteKey, Case] | None = None,
    param_resolvers: dict[str, Payload] | None = None,
    allowlist: dict[RouteKey, str] | None = None,
) -> list[Finding]:
    cases = CASES if cases is None else cases
    resolvers = RESOLVERS if param_resolvers is None else param_resolvers
    allowlist = ALLOWLIST if allowlist is None else allowlist
    findings: list[Finding] = []

    specs = discover_routes(app)
    known = {spec.key for spec in specs}
    for key, reason in allowlist.items():
        if not reason.strip():
            findings.append(Finding("bad-allowlist", *key, "an allowlist entry needs a reason"))
        if key not in known:
            findings.append(Finding("stale-allowlist", *key, "no such route any more; remove it"))

    swept: list[RouteSpec] = []
    for spec in specs:
        if spec.key in allowlist:
            continue
        if not spec.auth:
            findings.append(
                Finding(
                    "unclassified",
                    *spec.key,
                    "a route with no bearer authentication is neither swept nor allowlisted; "
                    "add it to ALLOWLIST with a reason (or put it behind get_tenant)",
                )
            )
            continue
        swept.append(spec)
    for key in cases:
        if key not in known:
            findings.append(Finding("stale-case", *key, "CASES entry for a route that is gone"))
    swept.sort(key=lambda s: cases.get(s.key, Case()).last)

    before = await _snapshot(client, owner, swept, cases, resolvers)

    prepared_routes: list[_Prepared] = []
    for spec in swept:
        prepared = _prepare(spec, owner, cases, resolvers, findings)
        if prepared is not None:
            prepared_routes.append(prepared)

    # 1. No token: 401, before anything is run as the intruder.
    for prepared in prepared_routes:
        response = await _send(client, prepared, None)
        if response.status_code != 401:
            findings.append(
                Finding(
                    "no-token",
                    *prepared.spec.key,
                    f"answered {response.status_code} without a bearer token, expected 401",
                )
            )
        for leaked in _leaks(response.text, owner, prepared.supplied):
            findings.append(
                Finding("leak", *prepared.spec.key, f"unauthenticated body has {leaked}")
            )

    # 2. The intruder, with the owner's ids.
    for prepared in prepared_routes:
        spec = prepared.spec
        case = cases.get(spec.key, Case())
        response = await _send(client, prepared, intruder.headers)
        if not _expected(spec, case, response.status_code):
            wanted = (
                sorted(case.expect)
                if case.expect
                else ("403/404" if spec.path_params else "a non-5xx answer that ran the handler")
            )
            hint = (
                " (422: the sweep sent an invalid request, fix its CASES entry)"
                if (response.status_code == 422)
                else ""
            )
            findings.append(
                Finding(
                    "wrong-status",
                    *spec.key,
                    f"{prepared.url} answered {response.status_code}, expected {wanted}"
                    f"{hint}: {response.text[:200]}",
                )
            )
        for leaked in _leaks(response.text, owner, prepared.supplied):
            findings.append(
                Finding(
                    "leak",
                    *spec.key,
                    f"{prepared.url} ({response.status_code}) returned the owner's {leaked!r}",
                )
            )

    # 3. The owner's data is untouched.
    after = await _snapshot(client, owner, swept, cases, resolvers)
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            method, _, url = name.partition(" ")
            findings.append(
                Finding("mutated", method, url, "the owner's view changed during the sweep")
            )
    return findings


def format_findings(findings: list[Finding]) -> str:
    return "\n".join(f"  {finding}" for finding in findings)
