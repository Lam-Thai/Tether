"""Shared fixtures for auth + tenant isolation, reused by every slice that
touches business-scoped data.
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from core.app import create_app
from core.config import settings
from core.db import async_session_factory
from core.tenant import get_signing_key_resolver
from integrations.clerk.fake import build_fake_signing_key_resolver
from models.business import Business
from models.user import User


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
def app(rsa_keypair):
    """The FastAPI app with Clerk's JWKS fetch swapped for the in-memory
    fake, so tests verify real signatures without any network call.
    """
    _, public_pem = rsa_keypair
    application = create_app()
    application.dependency_overrides[get_signing_key_resolver] = lambda: (
        build_fake_signing_key_resolver(public_pem)
    )
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
