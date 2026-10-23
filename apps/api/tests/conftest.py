"""Shared fixtures for auth + tenant isolation, reused by every slice that
touches business-scoped data.
"""

import os
import random
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from core.app import create_app
from core.billing import get_billing_gateway
from core.calendar import (
    get_calendar_api,
    get_calendar_config,
    get_oauth_state_signer,
    get_token_vault,
)
from core.config import settings
from core.db import async_session_factory, is_local_database
from core.llm import get_llm_client
from core.tenant import get_signing_key_resolver
from core.usage import SqlUsageStore, cost_rates, get_usage_guard, usage_limits
from integrations.clerk.fake import build_fake_signing_key_resolver
from integrations.google_calendar.fake import FakeGoogleCalendarApi
from integrations.llm.fake import FakeLLMClient
from integrations.stripe.fake import FakeBillingGateway
from integrations.stripe.port import SubscriptionInfo
from models.billing import Subscription
from models.business import Business
from models.usage import UsageCounter, UsageEvent
from models.user import User
from services.calendar import CalendarConfig
from services.oauth_state import OAuthStateSigner
from services.token_vault import TokenVault
from services.usage import DailyLimit, UsageGuard, UsageKind, UsageLimits


def pytest_configure(config: pytest.Config) -> None:
    """Refuse to run against a database the tests could damage.

    Tests create and delete tenants, so they run only with ENVIRONMENT=local or
    ci and, unless TEST_DATABASE_ALLOW_REMOTE=1 is set, only against a Postgres
    on this machine (`scripts/test.sh` points them at one).
    """
    if settings.environment == "production":
        raise pytest.UsageError(
            "Refusing to run tests with ENVIRONMENT=production. "
            "Use `sh scripts/test.sh` (local Postgres, test settings)."
        )
    host = make_url(settings.database_url).host
    if not is_local_database(settings.database_url) and not settings.test_database_allow_remote:
        raise pytest.UsageError(
            f"Refusing to run tests against a remote database ({host}). Point DATABASE_URL "
            "at a local Postgres (`sh scripts/test.sh` does), or set "
            "TEST_DATABASE_ALLOW_REMOTE=1 for a disposable test database."
        )


@pytest.fixture(scope="session")
def rsa_keypair() -> tuple[rsa.RSAPrivateKey, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_key, public_pem


@pytest.fixture
def mint_token(rsa_keypair):
    """Signs a real RS256 token with the test keypair. Defaults match what
    `verify_principal` requires; pass overrides to build an invalid token.
    """
    private_key, _ = rsa_keypair

    def _mint(
        *,
        subject: str = "user_test",
        email: str | None = "owner@example.com",
        issuer: str = settings.clerk_issuer,
        azp: str | None = None,
        expires_delta: timedelta = timedelta(minutes=5),
        signing_key: rsa.RSAPrivateKey | None = None,
        extra_claims: dict | None = None,
    ) -> str:
        now = datetime.now(UTC)
        payload = {"sub": subject, "iss": issuer, "iat": now, "exp": now + expires_delta}
        if email is not None:
            payload["email"] = email
        if azp is not None:
            payload["azp"] = azp
        payload.update(extra_claims or {})
        return jwt.encode(payload, signing_key or private_key, algorithm="RS256")

    return _mint


@pytest.fixture
def billing_gateway() -> FakeBillingGateway:
    return FakeBillingGateway()


@pytest.fixture
def llm() -> FakeLLMClient:
    return FakeLLMClient()


@dataclass
class UsageHarness:
    """The `UsageGuard` the test app uses: real Postgres store, generous limits,
    and a clock pinned to a far-future UTC day unique to this test, so tests
    never share counters with each other or eat the dev database's real global
    quota. Tighten limits per test with `set_daily(...)` / `set_concurrency(...)`.
    """

    day: datetime
    limits: UsageLimits = field(
        default_factory=lambda: replace(
            usage_limits(settings),
            daily={
                kind: DailyLimit(per_day=1_000_000, per_business_per_day=1_000_000)
                for kind in usage_limits(settings).daily
            },
            max_concurrent_interactions=1_000_000,
            max_concurrent_interactions_per_business=1_000_000,
        )
    )

    def guard(self) -> UsageGuard:
        return UsageGuard(
            SqlUsageStore(async_session_factory),
            self.limits,
            cost_rates(settings),
            clock=lambda: self.day,
        )

    def set_daily(self, kind: UsageKind, *, per_day: int, per_business_per_day: int) -> None:
        daily = dict(self.limits.daily)
        daily[kind] = DailyLimit(per_day, per_business_per_day)
        self.limits = replace(self.limits, daily=daily)

    def set_concurrency(self, *, total: int, per_business: int) -> None:
        self.limits = replace(
            self.limits,
            max_concurrent_interactions=total,
            max_concurrent_interactions_per_business=per_business,
        )


@pytest.fixture
async def usage() -> AsyncIterator[UsageHarness]:
    start = datetime(2200, 1, 1, 12, tzinfo=UTC)
    harness = UsageHarness(day=start + timedelta(days=random.randrange(1_000_000)))
    yield harness
    # Business-scoped rows cascade with the test businesses; the global ones don't.
    async with async_session_factory() as session:
        await session.execute(
            delete(UsageCounter).where(
                UsageCounter.business_id.is_(None),
                UsageCounter.window_date == harness.day.date(),
            )
        )
        await session.execute(
            delete(UsageEvent).where(
                UsageEvent.business_id.is_(None),
                UsageEvent.created_at >= harness.day - timedelta(days=1),
                UsageEvent.created_at < harness.day + timedelta(days=1),
            )
        )
        await session.commit()


@pytest.fixture
def app(rsa_keypair, billing_gateway, llm, usage):
    """The FastAPI app with Clerk's JWKS fetch and Stripe swapped for
    in-memory fakes, so tests verify real signatures and billing flows
    without any network call.
    """
    _, public_pem = rsa_keypair
    application = create_app()
    application.dependency_overrides[get_signing_key_resolver] = lambda: (
        build_fake_signing_key_resolver(public_pem)
    )
    application.dependency_overrides[get_billing_gateway] = lambda: billing_gateway
    application.dependency_overrides[get_llm_client] = lambda: llm
    # Built per request, so a test can tighten `usage` limits between calls.
    application.dependency_overrides[get_usage_guard] = usage.guard
    yield application
    application.dependency_overrides.clear()


@dataclass(frozen=True)
class TenantFixture:
    token: str
    external_auth_id: str
    email: str
    business_id: uuid.UUID


@pytest.fixture
async def two_tenants(app, mint_token) -> AsyncIterator[tuple[TenantFixture, TenantFixture]]:
    """Onboards two fully separate owners (each with their own `users` and
    `businesses` row) through the real API, for cross-tenant isolation
    tests. Cleans up both rows afterward.
    """

    async def _onboard(label: str) -> TenantFixture:
        external_auth_id = f"user_{uuid.uuid4().hex}"
        email = f"{label}.{uuid.uuid4().hex[:8]}@example.com"
        token = mint_token(subject=external_auth_id, email=email)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/business",
                json={
                    "name": f"{label.title()} Business",
                    "industry": "Salon",
                    "address": "123 Main St",
                    "timezone": "America/New_York",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 201, response.text
        return TenantFixture(
            token=token,
            external_auth_id=external_auth_id,
            email=email,
            business_id=uuid.UUID(response.json()["id"]),
        )

    tenant_a = await _onboard("alpha")
    tenant_b = await _onboard("beta")

    yield tenant_a, tenant_b

    async with async_session_factory() as session:
        # Businesses first — `owner_id` FKs to `users.id` with no cascade.
        await session.execute(
            delete(Business).where(Business.id.in_([tenant_a.business_id, tenant_b.business_id]))
        )
        await session.execute(
            delete(User).where(
                User.external_auth_id.in_([tenant_a.external_auth_id, tenant_b.external_auth_id])
            )
        )
        await session.commit()


@pytest.fixture
async def entitled_tenants(
    app, billing_gateway, two_tenants
) -> tuple[TenantFixture, TenantFixture]:
    """`two_tenants`, each with a trial started through the fake billing gateway,
    so business-feature routes (which require an entitled plan) are reachable.
    Cleanup is the business delete in `two_tenants`, which cascades to the
    subscription, agent, interactions and messages.
    """

    async def _start_trial(client: AsyncClient, tenant: TenantFixture) -> None:
        headers = {"Authorization": f"Bearer {tenant.token}"}
        response = await client.post(
            "/api/v1/billing/checkout", json={"choice": "trial"}, headers=headers
        )
        assert response.status_code == 200, response.text
        async with async_session_factory() as session:
            row = (
                await session.execute(
                    select(Subscription).where(Subscription.business_id == tenant.business_id)
                )
            ).scalar_one()
        assert row.stripe_customer_id and row.pending_checkout_session_id
        now = datetime.now(UTC)
        billing_gateway.complete_checkout(
            row.pending_checkout_session_id,
            SubscriptionInfo(
                id=billing_gateway.new_subscription_id(),
                customer_id=row.stripe_customer_id,
                status="trialing",
                price_id="price_fake",
                trial_start=now,
                trial_end=now + timedelta(days=14),
                current_period_end=now + timedelta(days=14),
                cancel_at_period_end=False,
            ),
        )
        sync = await client.post(
            f"/api/v1/billing/checkout/{row.pending_checkout_session_id}/sync", headers=headers
        )
        assert sync.status_code == 200, sync.text

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for tenant in two_tenants:
            await _start_trial(client, tenant)
    return two_tenants


@dataclass
class CalendarHarness:
    """A configured Google Calendar for the test app: the fake provider, a vault on a
    random test key and a signer on a test secret, wired in as the app's dependencies.
    Tests script Google through `api` and change `config` (e.g. `status_check_seconds`)
    between calls. Booking tests compose `GoogleCalendarGateway(harness.api,
    harness.vault, usage.guard())` to exercise metering for real."""

    api: FakeGoogleCalendarApi
    vault: TokenVault
    state_secret: str
    config: CalendarConfig
    key: bytes  # the vault's key `test`, for building a rotated vault

    def signer(self, clock=None) -> OAuthStateSigner:
        if clock is None:
            return OAuthStateSigner(self.state_secret, 600)
        return OAuthStateSigner(self.state_secret, 600, clock=clock)


@pytest.fixture
def calendar(app) -> CalendarHarness:
    key = os.urandom(32)
    harness = CalendarHarness(
        api=FakeGoogleCalendarApi(),
        vault=TokenVault({"test": key}, "test"),
        key=key,
        state_secret="test-state-secret-" + uuid.uuid4().hex,
        config=CalendarConfig(
            client_configured=True,
            redirect_uri="https://example.com/dashboard/settings/calendar/callback",
            status_check_seconds=300,
        ),
    )
    app.dependency_overrides[get_calendar_api] = lambda: harness.api
    app.dependency_overrides[get_token_vault] = lambda: harness.vault
    app.dependency_overrides[get_oauth_state_signer] = lambda: harness.signer()
    app.dependency_overrides[get_calendar_config] = lambda: harness.config
    return harness
