"""Integration tests for `GET /api/v1/me` against a real Postgres."""

import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from core.db import async_session_factory
from models.user import User


async def test_me_requires_a_bearer_token(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_me_returns_the_caller_with_no_business_before_onboarding(app, mint_token):
    external_auth_id = f"user_{uuid.uuid4().hex}"
    token = mint_token(subject=external_auth_id, email="owner@example.com")

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/me", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 200
        body = response.json()
        assert body["email"] == "owner@example.com"
        assert body["business"] is None
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(User).where(User.external_auth_id == external_auth_id))
            await session.commit()


async def test_me_upserts_the_same_user_row_across_requests(app, mint_token):
    external_auth_id = f"user_{uuid.uuid4().hex}"
    token = mint_token(subject=external_auth_id, email="owner@example.com")

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
            second = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

        assert first.json()["id"] == second.json()["id"]
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(User).where(User.external_auth_id == external_auth_id))
            await session.commit()


async def test_me_reflects_the_tenants_business_after_onboarding(app, two_tenants):
    tenant_a, _ = two_tenants

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {tenant_a.token}"}
        )

    assert response.status_code == 200
    assert response.json()["business"]["id"] == str(tenant_a.business_id)
