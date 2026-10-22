"""`/api/v1/services` against a real Postgres."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from core.directory import get_service_max


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_crud_happy_path_and_duplicate_names(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    headers = _auth(tenant_a)

    created = await client.post(
        "/api/v1/services",
        json={"name": "  Haircut ", "duration_minutes": 45, "price": "40", "description": " "},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    service = created.json()
    assert (service["name"], service["price"], service["description"], service["active"]) == (
        "Haircut",
        "40.00",
        None,
        True,
    )
    free = await client.post(
        "/api/v1/services", json={"name": "Consultation", "duration_minutes": 15}, headers=headers
    )
    assert free.status_code == 201 and free.json()["price"] is None

    clash = await client.post(
        "/api/v1/services", json={"name": "HAIRCUT", "duration_minutes": 30}, headers=headers
    )
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "service_name_taken"
    rename = await client.patch(
        f"/api/v1/services/{free.json()['id']}", json={"name": "haircut"}, headers=headers
    )
    assert rename.status_code == 409 and rename.json()["error"]["code"] == "service_name_taken"

    listed = (await client.get("/api/v1/services", headers=headers)).json()
    assert (listed["currency"], listed["max_services"]) == ("USD", 100)
    assert [s["name"] for s in listed["services"]] == ["Consultation", "Haircut"]

    patched = await client.patch(
        f"/api/v1/services/{service['id']}",
        json={"price": "45.5", "duration_minutes": 50, "active": False, "description": "Wash"},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert (body["price"], body["duration_minutes"], body["active"]) == ("45.50", 50, False)
    cleared = await client.patch(
        f"/api/v1/services/{service['id']}", json={"price": None}, headers=headers
    )
    assert cleared.json()["price"] is None and cleared.json()["description"] == "Wash"
    inactive_still_listed = (await client.get("/api/v1/services", headers=headers)).json()
    assert len(inactive_still_listed["services"]) == 2

    deleted = await client.delete(f"/api/v1/services/{service['id']}", headers=headers)
    assert deleted.status_code == 204 and deleted.content == b""
    again = await client.delete(f"/api/v1/services/{service['id']}", headers=headers)
    assert again.status_code == 404 and again.json()["error"]["code"] == "service_not_found"


async def test_every_route_is_402_without_a_plan_and_401_without_a_token(client, two_tenants):
    tenant, _ = two_tenants
    some_id = uuid.uuid4()
    calls = [
        ("GET", "/api/v1/services", None),
        ("POST", "/api/v1/services", {"name": "Cut", "duration_minutes": 30}),
        ("PATCH", f"/api/v1/services/{some_id}", {"active": False}),
        ("DELETE", f"/api/v1/services/{some_id}", None),
    ]
    for method, path, body in calls:
        response = await client.request(method, path, json=body, headers=_auth(tenant))
        assert response.status_code == 402, path
        assert response.json()["error"]["code"] == "subscription_required"
        assert (await client.request(method, path, json=body)).status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"duration_minutes": 30},
        {"name": "Cut"},
        {"name": "  ", "duration_minutes": 30},
        {"name": "x" * 81, "duration_minutes": 30},
        {"name": "Cut", "duration_minutes": 0},
        {"name": "Cut", "duration_minutes": 1441},
        {"name": "Cut", "duration_minutes": 30, "price": "10.999"},
        {"name": "Cut", "duration_minutes": 30, "price": "-1"},
        {"name": "Cut", "duration_minutes": 30, "price": "NaN"},
        {"name": "Cut", "duration_minutes": 30, "description": "x" * 501},
        {"name": "Cut", "duration_minutes": 30, "business_id": str(uuid.uuid4())},
    ],
)
async def test_create_validation_is_422(client, entitled_tenants, payload):
    response = await client.post(
        "/api/v1/services", json=payload, headers=_auth(entitled_tenants[0])
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    "payload",
    [
        {"name": None},
        {"duration_minutes": None},
        {"duration_minutes": 0},
        {"active": None},
        {"price": "1.234"},
        {"business_id": str(uuid.uuid4())},
    ],
)
async def test_patch_validation_is_422(client, entitled_tenants, payload):
    tenant_a, _ = entitled_tenants
    service = (
        await client.post(
            "/api/v1/services",
            json={"name": "Cut", "duration_minutes": 30},
            headers=_auth(tenant_a),
        )
    ).json()

    response = await client.patch(
        f"/api/v1/services/{service['id']}", json=payload, headers=_auth(tenant_a)
    )

    assert response.status_code == 422


async def test_cap_is_409_and_per_business(app, client, entitled_tenants):
    tenant_a, tenant_b = entitled_tenants
    app.dependency_overrides[get_service_max] = lambda: 2
    for name in ("One", "Two"):
        created = await client.post(
            "/api/v1/services", json={"name": name, "duration_minutes": 10}, headers=_auth(tenant_a)
        )
        assert created.status_code == 201
    new = {"name": "Three", "duration_minutes": 10}

    over = await client.post("/api/v1/services", json=new, headers=_auth(tenant_a))
    other = await client.post("/api/v1/services", json=new, headers=_auth(tenant_b))

    assert over.status_code == 409 and over.json()["error"]["code"] == "service_limit_reached"
    assert other.status_code == 201
    listed = (await client.get("/api/v1/services", headers=_auth(tenant_a))).json()
    assert listed["max_services"] == 2


async def test_tenant_b_cannot_see_or_touch_tenant_a(client, entitled_tenants):
    tenant_a, tenant_b = entitled_tenants
    service = (
        await client.post(
            "/api/v1/services",
            json={"name": "Secret cut", "duration_minutes": 30, "price": "99"},
            headers=_auth(tenant_a),
        )
    ).json()
    url = f"/api/v1/services/{service['id']}"

    b_list = await client.get("/api/v1/services", headers=_auth(tenant_b))
    patch = await client.patch(
        url, json={"name": "hacked", "active": False}, headers=_auth(tenant_b)
    )
    delete = await client.delete(url, headers=_auth(tenant_b))
    # Same name in B is not a clash with A's.
    same_name = await client.post(
        "/api/v1/services",
        json={"name": "secret cut", "duration_minutes": 20},
        headers=_auth(tenant_b),
    )

    assert b_list.json()["services"] == []
    for response in (patch, delete):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "service_not_found"
    assert same_name.status_code == 201
    after = (await client.get("/api/v1/services", headers=_auth(tenant_a))).json()["services"]
    assert after == [service]  # unchanged, still there
