"""`/api/v1/faqs` against a real Postgres."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from core.faqs import get_faq_max


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_crud_happy_path(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    headers = _auth(tenant_a)

    created = await client.post(
        "/api/v1/faqs",
        json={"question": "  Do you take walk-ins?  ", "answer": "Yes.", "category": "  "},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    faq = created.json()
    assert (faq["question"], faq["category"], faq["active"]) == (
        "Do you take walk-ins?",
        None,
        True,
    )

    listed = (await client.get("/api/v1/faqs", headers=headers)).json()
    assert listed["max_faqs"] == 200
    assert [f["id"] for f in listed["faqs"]] == [faq["id"]]

    patched = await client.patch(
        f"/api/v1/faqs/{faq['id']}",
        json={"answer": "Before 3 PM.", "category": "Booking", "active": False},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert (body["answer"], body["category"], body["active"]) == ("Before 3 PM.", "Booking", False)
    assert body["question"] == faq["question"]  # untouched
    cleared = await client.patch(
        f"/api/v1/faqs/{faq['id']}", json={"category": None}, headers=headers
    )
    assert cleared.json()["category"] is None and cleared.json()["active"] is False

    deleted = await client.delete(f"/api/v1/faqs/{faq['id']}", headers=headers)
    assert deleted.status_code == 204 and deleted.content == b""
    assert (await client.get("/api/v1/faqs", headers=headers)).json()["faqs"] == []
    again = await client.delete(f"/api/v1/faqs/{faq['id']}", headers=headers)
    assert again.status_code == 404 and again.json()["error"]["code"] == "faq_not_found"


async def test_every_route_is_402_without_a_plan_and_401_without_a_token(client, two_tenants):
    tenant, _ = two_tenants
    some_id = uuid.uuid4()
    calls = [
        ("GET", "/api/v1/faqs", None),
        ("POST", "/api/v1/faqs", {"question": "Q", "answer": "A"}),
        ("PATCH", f"/api/v1/faqs/{some_id}", {"active": False}),
        ("DELETE", f"/api/v1/faqs/{some_id}", None),
    ]
    for method, path, body in calls:
        response = await client.request(method, path, json=body, headers=_auth(tenant))
        assert response.status_code == 402, path
        assert response.json()["error"]["code"] == "subscription_required"
        assert (await client.request(method, path, json=body)).status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"answer": "A"},
        {"question": "Q"},
        {"question": "  ", "answer": "A"},
        {"question": "Q", "answer": ""},
        {"question": "x" * 301, "answer": "A"},
        {"question": "Q", "answer": "x" * 1001},
        {"question": "Q", "answer": "A", "category": "x" * 61},
        {"question": "Q", "answer": "A", "business_id": str(uuid.uuid4())},
    ],
)
async def test_create_validation_is_422(client, entitled_tenants, payload):
    response = await client.post("/api/v1/faqs", json=payload, headers=_auth(entitled_tenants[0]))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    "payload",
    [{"question": None}, {"answer": ""}, {"active": None}, {"business_id": str(uuid.uuid4())}],
)
async def test_patch_validation_is_422(client, entitled_tenants, payload):
    tenant_a, _ = entitled_tenants
    faq = (
        await client.post(
            "/api/v1/faqs", json={"question": "Q", "answer": "A"}, headers=_auth(tenant_a)
        )
    ).json()

    response = await client.patch(
        f"/api/v1/faqs/{faq['id']}", json=payload, headers=_auth(tenant_a)
    )

    assert response.status_code == 422


async def test_cap_is_409_and_per_business(app, client, entitled_tenants):
    tenant_a, tenant_b = entitled_tenants
    app.dependency_overrides[get_faq_max] = lambda: 2
    new = {"question": "Q", "answer": "A"}
    for _ in range(2):
        created = await client.post("/api/v1/faqs", json=new, headers=_auth(tenant_a))
        assert created.status_code == 201

    over = await client.post("/api/v1/faqs", json=new, headers=_auth(tenant_a))
    other = await client.post("/api/v1/faqs", json=new, headers=_auth(tenant_b))

    assert over.status_code == 409 and over.json()["error"]["code"] == "faq_limit_reached"
    assert other.status_code == 201
    assert (await client.get("/api/v1/faqs", headers=_auth(tenant_a))).json()["max_faqs"] == 2


async def test_tenant_b_cannot_see_or_touch_tenant_a(client, entitled_tenants):
    tenant_a, tenant_b = entitled_tenants
    faq = (
        await client.post(
            "/api/v1/faqs",
            json={"question": "Secret?", "answer": "A's answer.", "category": "Policies"},
            headers=_auth(tenant_a),
        )
    ).json()
    url = f"/api/v1/faqs/{faq['id']}"

    b_list = await client.get("/api/v1/faqs", headers=_auth(tenant_b))
    patch = await client.patch(
        url, json={"answer": "hacked", "active": False}, headers=_auth(tenant_b)
    )
    delete = await client.delete(url, headers=_auth(tenant_b))

    assert b_list.json()["faqs"] == []
    for response in (patch, delete):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "faq_not_found"
    after = (await client.get("/api/v1/faqs", headers=_auth(tenant_a))).json()["faqs"]
    assert after == [faq]  # unchanged, still there
