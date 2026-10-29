"""Rate limiting over HTTP: the 429 envelope, per-business isolation, per-IP webhooks, the
off switch, and a coverage check that every mutating route carries a limiter."""

from dataclasses import replace

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from core.config import settings as app_settings
from core.rate_limit import (
    RATE_LIMIT_DEPENDENCIES,
    RateRules,
    get_rate_rules,
    rate_rules,
    warn_if_client_ip_unknown,
)
from core.twilio import SMS_STATUS_PATH
from integrations.llm.fake import agent_turn
from services.rate_limit import RateRule

# One-day windows so a test can't straddle a window boundary.
DAY = 86_400
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


def _tighten(app, **limits: int) -> None:
    rules = rate_rules()
    changes = {name: RateRule(name, limit, DAY) for name, limit in limits.items()}
    app.dependency_overrides[get_rate_rules] = lambda: replace(rules, **changes)


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _send(client, tenant, message="hello"):
    return await client.post(
        "/api/v1/agent/test",
        json={"interaction_id": None, "message": message},
        headers=_auth(tenant),
    )


async def test_agent_test_returns_429_with_the_envelope_and_retry_after(
    app, client, llm, entitled_tenants
):
    tenant_a, _ = entitled_tenants
    _tighten(app, agent_test=2)
    llm.queue(agent_turn("one"), agent_turn("two"), agent_turn("three"))

    first, second, third = [await _send(client, tenant_a) for _ in range(3)]

    assert (first.status_code, second.status_code) == (200, 200)
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "rate_limited"
    assert third.json()["error"]["message"]
    assert 1 <= int(third.headers["Retry-After"]) <= DAY
    assert len(llm.requests) == 2  # the refused message never reached the model


async def test_agent_test_limit_is_per_business(app, client, llm, entitled_tenants):
    tenant_a, tenant_b = entitled_tenants
    _tighten(app, agent_test=1)
    llm.queue(agent_turn("a"), agent_turn("b"))

    assert (await _send(client, tenant_a)).status_code == 200
    assert (await _send(client, tenant_a)).status_code == 429
    assert (await _send(client, tenant_b)).status_code == 200


async def test_voice_preview_has_its_own_stricter_limit(app, client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    _tighten(app, voice_preview=1)
    url = "/api/v1/agent/voice-preview"

    first = await client.post(url, json={"voice": "Puck"}, headers=_auth(tenant_a))
    second = await client.post(url, json={"voice": "Puck"}, headers=_auth(tenant_a))

    assert first.status_code != 429
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    # Other mutations are untouched by the preview rule.
    patch = await client.patch("/api/v1/agent", json={"name": "Sam"}, headers=_auth(tenant_a))
    assert patch.status_code == 200


async def test_mutations_are_limited_per_business_and_not_shared(app, client, entitled_tenants):
    tenant_a, tenant_b = entitled_tenants
    _tighten(app, mutations=2)

    async def create(tenant, n):
        return await client.post(
            "/api/v1/faqs",
            json={"question": f"Q{n}?", "answer": "A."},
            headers=_auth(tenant),
        )

    assert [(await create(tenant_a, n)).status_code for n in range(2)] == [201, 201]
    blocked = await create(tenant_a, 3)
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    # Business B has its own budget, and A can still read.
    assert (await create(tenant_b, 1)).status_code == 201
    assert (await client.get("/api/v1/faqs", headers=_auth(tenant_a))).status_code == 200


async def test_reads_are_never_limited(app, client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    _tighten(app, mutations=0, agent_test=0)

    for _ in range(5):
        assert (await client.get("/api/v1/agent", headers=_auth(tenant_a))).status_code == 200


async def test_webhooks_are_limited_per_ip_before_the_signature_check(app, client):
    _tighten(app, webhooks=2)

    # Unsigned requests: refused by the signature check until the IP budget runs out.
    codes = [(await client.post(SMS_STATUS_PATH, content=b"")).status_code for _ in range(3)]

    assert codes[:2] == [403, 403]
    assert codes[2] == 429


async def test_webhook_limit_follows_the_trusted_header_only_when_configured(
    app, client, monkeypatch
):
    from core.config import settings

    _tighten(app, webhooks=1)
    monkeypatch.setattr(settings, "trusted_client_ip_header", "x-real-ip")

    def hit(ip):
        return client.post(SMS_STATUS_PATH, content=b"", headers={"x-real-ip": ip})

    assert (await hit("203.0.113.7")).status_code == 403
    assert (await hit("203.0.113.7")).status_code == 429
    assert (await hit("203.0.113.8")).status_code == 403  # another client, its own budget

    monkeypatch.setattr(settings, "trusted_client_ip_header", "")
    # Header ignored: everyone is the socket peer, who is already over the limit.
    assert (await client.post(SMS_STATUS_PATH, content=b"")).status_code == 403


async def test_a_forwarded_for_list_is_keyed_by_its_last_hop_not_the_clients_claim(
    app, client, monkeypatch
):
    from core.config import settings

    _tighten(app, webhooks=1)
    monkeypatch.setattr(settings, "trusted_client_ip_header", "x-forwarded-for")

    def hit(claimed: str):
        # The client controls everything before the hop our proxy appended.
        headers = {"x-forwarded-for": f"{claimed}, 198.51.100.9"}
        return client.post(SMS_STATUS_PATH, content=b"", headers=headers)

    assert (await hit("10.0.0.1")).status_code == 403
    # A fresh made-up first hop does not buy a fresh budget.
    assert (await hit("10.0.0.2")).status_code == 429


async def test_the_off_switch_disables_every_limit(app, client, llm, entitled_tenants):
    tenant_a, _ = entitled_tenants
    _tighten(app, agent_test=0, mutations=0, webhooks=0)
    app.dependency_overrides[get_rate_rules] = lambda: replace(
        rate_rules(), enabled=False, agent_test=RateRule("agent_test", 0, DAY)
    )
    llm.queue(agent_turn("hi"))

    assert (await _send(client, tenant_a)).status_code == 200
    assert (await client.post(SMS_STATUS_PATH, content=b"")).status_code == 403


def test_the_rules_come_from_settings():
    rules: RateRules = rate_rules()

    assert rules.enabled is True
    assert (rules.agent_test.limit, rules.voice_preview.limit) == (30, 6)
    assert (rules.mutations.limit, rules.webhooks.limit) == (120, 600)
    assert {r.window_seconds for r in (rules.agent_test, rules.mutations, rules.webhooks)} == {60}


@pytest.mark.parametrize(
    ("environment", "header", "enabled", "warns"),
    [
        ("production", "", True, True),
        ("production", "x-real-ip", True, False),
        ("production", "", False, False),
        ("local", "", True, False),
    ],
    ids=["prod-no-header", "prod-header", "prod-disabled", "local"],
)
def test_production_without_a_client_ip_header_warns_at_startup(
    environment, header, enabled, warns, caplog
):
    config = app_settings.model_copy(
        update={
            "environment": environment,
            "trusted_client_ip_header": header,
            "rate_limit_enabled": enabled,
        }
    )

    with caplog.at_level("WARNING", logger="tether.rate_limit"):
        assert warn_if_client_ip_unknown(config) is warns

    assert ("rate_limit_client_ip_header_unset" in caplog.text) is warns


# A mutating route with no limiter must be listed here WITH a reason. Empty on purpose:
# a new POST/PUT/PATCH/DELETE route fails this test until it carries a limiter.
RATE_LIMIT_EXEMPT: dict[tuple[str, str], str] = {}


def _calls(dependant: Dependant) -> set:
    found = set()
    for dep in dependant.dependencies:
        found.add(dep.call)
        found |= _calls(dep)
    return found


def test_every_mutating_route_carries_a_rate_limit(app):
    unprotected = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted((route.methods or set()) & MUTATING):
            if (method, route.path) in RATE_LIMIT_EXEMPT:
                continue
            if not _calls(route.dependant) & RATE_LIMIT_DEPENDENCIES:
                unprotected.append(f"{method} {route.path}")

    assert unprotected == [], f"mutating routes without a rate limit: {unprotected}"


def test_the_exempt_list_has_no_stale_entries(app):
    mutating = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in (route.methods or set()) & MUTATING
    }

    assert set(RATE_LIMIT_EXEMPT) <= mutating
    assert all(RATE_LIMIT_EXEMPT.values())
