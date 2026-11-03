"""Tenant B attacks tenant A through EVERY route of the real app (see `isolation.py`)."""

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from core.config import settings
from core.voice import get_numbers_api
from integrations.twilio.fake import FakeNumbersApi
from tests.security.isolation import (
    ALLOWLIST,
    CASES,
    WEBHOOK_REASON,
    discover_routes,
    format_findings,
    seed_tenant,
    sweep,
    webhook_routes,
)


@pytest.fixture
def twilio_on(app, monkeypatch) -> FakeNumbersApi:
    """Phone numbers configured (fake account), so their routes reach the tenant check."""
    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "c" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr("sweep-token"))
    monkeypatch.setattr(settings, "twilio_from_number", "+15005550006")
    monkeypatch.setattr(settings, "api_public_base_url", "https://api.example.com")
    fake = FakeNumbersApi(trial=True)
    app.dependency_overrides[get_numbers_api] = lambda: fake
    return fake


async def test_no_tenant_can_reach_another_tenants_data_through_any_route(
    app, entitled_tenants, calendar, llm, billing_gateway, twilio_on
):
    tenant_a, tenant_b = entitled_tenants
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        owner = await seed_tenant(
            client, tenant_a, llm=llm, billing_gateway=billing_gateway, calendar=calendar
        )
        intruder = await seed_tenant(
            client, tenant_b, llm=llm, billing_gateway=billing_gateway, calendar=calendar
        )
        # The owner's number is on the account too, so a claim is refused by the database's
        # one-owner rule rather than by "not on the account".
        twilio_on.add_number(owner.ids["phone_number"], sid=owner.ids["provider_number_sid"])
        findings = await sweep(client, app, owner, intruder)

    assert findings == [], "\n" + format_findings(findings)


def test_every_route_is_swept_or_allowlisted_with_a_reason(app):
    specs = discover_routes(app)
    routes = {spec.key for spec in specs}
    unclassified = [spec.key for spec in specs if spec.key not in ALLOWLIST and not spec.auth]
    assert unclassified == []
    assert all(reason.strip() for reason in ALLOWLIST.values())
    assert set(ALLOWLIST) <= routes
    assert set(CASES) <= routes
    # A floor, so a discovery regression that silently finds fewer routes fails loudly
    # (55 tenant routes when the sweep landed; raise this as routes are added).
    assert sum(spec.auth for spec in specs) >= 55
    # Hidden (include_in_schema=False) routes are visible to the discovery.
    assert ("POST", "/api/v1/webhooks/stripe") in routes
    assert ("GET", "/api/v1/media/{media_token}") in routes


def test_allowlisted_webhooks_are_exactly_the_ones_the_signature_sweep_covers(app):
    allowlisted = {key for key, reason in ALLOWLIST.items() if reason == WEBHOOK_REASON}
    covered = {spec.key for spec in webhook_routes(app)}
    assert allowlisted == covered
