"""Integration tests for `/api/v1/business` against a real Postgres.

The cross-tenant cases here are the acceptance criterion from issue #3: a
forged/guessed `business_id` belonging to another tenant must 404, never
leak existence, and never be reachable by changing a client-supplied id.
"""

import uuid

from httpx import ASGITransport, AsyncClient


async def test_get_business_requires_a_bearer_token(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/business")

    assert response.status_code == 401


async def test_get_business_404s_before_onboarding(app, mint_token):
    token = mint_token(subject=f"user_{uuid.uuid4().hex}")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/business", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "business_not_found"


async def test_post_business_rejects_invalid_timezone(app, mint_token):
    token = mint_token(subject=f"user_{uuid.uuid4().hex}")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/business",
            json={
                "name": "Alpha Salon",
                "industry": "Salon",
                "address": "123 Main St",
                "timezone": "Not/AZone",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422


async def test_owner_can_read_and_patch_their_own_business(app, two_tenants):
    tenant_a, _ = two_tenants

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        get_response = await client.get(
            "/api/v1/business", headers={"Authorization": f"Bearer {tenant_a.token}"}
        )
        patch_response = await client.patch(
            f"/api/v1/business/{tenant_a.business_id}",
            json={"name": "Renamed Salon"},
            headers={"Authorization": f"Bearer {tenant_a.token}"},
        )

    assert get_response.status_code == 200
    assert patch_response.status_code == 200
    assert patch_response.json()["name"] == "Renamed Salon"


async def test_second_owner_cannot_create_a_second_business_once_they_have_one(
    app, two_tenants
):
    tenant_a, _ = two_tenants

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/business",
            json={
                "name": "Second Business",
                "industry": "Salon",
                "address": "456 Side St",
                "timezone": "America/New_York",
            },
            headers={"Authorization": f"Bearer {tenant_a.token}"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "business_already_exists"


async def test_patching_another_tenants_business_id_404s(app, two_tenants):
    """Tenant B forges tenant A's `business_id` in the PATCH path — the
    hard rule: the backend must never trust a client-supplied business_id.
    """
    tenant_a, tenant_b = two_tenants

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/api/v1/business/{tenant_a.business_id}",
            json={"name": "Hijacked"},
            headers={"Authorization": f"Bearer {tenant_b.token}"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "business_not_found"


async def test_getting_a_nonexistent_business_id_and_another_tenants_404_identically(
    app, two_tenants
):
    """No existence leak: a made-up id and a real-but-foreign id must be
    indistinguishable from the response.
    """
    tenant_a, tenant_b = two_tenants

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        made_up = await client.patch(
            f"/api/v1/business/{uuid.uuid4()}",
            json={"name": "Hijacked"},
            headers={"Authorization": f"Bearer {tenant_b.token}"},
        )
        real_but_foreign = await client.patch(
            f"/api/v1/business/{tenant_a.business_id}",
            json={"name": "Hijacked"},
            headers={"Authorization": f"Bearer {tenant_b.token}"},
        )

    assert made_up.status_code == real_but_foreign.status_code == 404
    assert made_up.json() == real_but_foreign.json()


async def test_each_tenant_only_sees_their_own_business_via_get(app, two_tenants):
    tenant_a, tenant_b = two_tenants

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response_a = await client.get(
            "/api/v1/business", headers={"Authorization": f"Bearer {tenant_a.token}"}
        )
        response_b = await client.get(
            "/api/v1/business", headers={"Authorization": f"Bearer {tenant_b.token}"}
        )

    assert response_a.json()["id"] == str(tenant_a.business_id)
    assert response_b.json()["id"] == str(tenant_b.business_id)
    assert response_a.json()["id"] != response_b.json()["id"]
