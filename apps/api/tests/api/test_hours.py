"""`/api/v1/business/hours` against a real Postgres."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _week(open_from="09:00", open_to="17:00", closed=(0, 6)) -> dict:
    return {
        "days": [
            {"day_of_week": d, "is_closed": True}
            if d in closed
            else {"day_of_week": d, "is_closed": False, "opens_at": open_from, "closes_at": open_to}
            for d in range(7)
        ]
    }


async def test_get_put_get_and_cross_tenant(client, entitled_tenants):
    tenant_a, tenant_b = entitled_tenants

    unset = (await client.get("/api/v1/business/hours", headers=_auth(tenant_a))).json()
    assert unset["configured"] is False and unset["days"] == []
    assert unset["timezone"]

    saved = await client.put("/api/v1/business/hours", json=_week(), headers=_auth(tenant_a))
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["configured"] is True
    assert [d["day_of_week"] for d in body["days"]] == list(range(7))
    assert body["days"][0] == {
        "day_of_week": 0,
        "is_closed": True,
        "opens_at": None,
        "closes_at": None,
    }
    assert body["days"][1]["opens_at"] == "09:00:00"
    assert (await client.get("/api/v1/business/hours", headers=_auth(tenant_a))).json() == body

    overwritten = await client.put(
        "/api/v1/business/hours", json=_week("08:00", "12:30", closed=()), headers=_auth(tenant_a)
    )
    assert overwritten.json()["days"][0]["closes_at"] == "12:30:00"

    # B neither sees nor overwrites A's week, and B's own PUT leaves A untouched.
    b_before = (await client.get("/api/v1/business/hours", headers=_auth(tenant_b))).json()
    assert b_before["configured"] is False
    b_put = await client.put(
        "/api/v1/business/hours", json=_week("10:00", "11:00", closed=(3,)), headers=_auth(tenant_b)
    )
    assert b_put.status_code == 200 and b_put.json()["days"][3]["is_closed"] is True
    a_after = (await client.get("/api/v1/business/hours", headers=_auth(tenant_a))).json()
    assert a_after == overwritten.json()


async def test_hours_routes_do_not_collide_with_business_routes(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    headers = _auth(tenant_a)

    hours = await client.get("/api/v1/business/hours", headers=headers)
    business = await client.get("/api/v1/business", headers=headers)
    patch = await client.patch(
        f"/api/v1/business/{tenant_a.business_id}", json={"name": "Still works"}, headers=headers
    )
    patch_hours = await client.patch("/api/v1/business/hours", json={"name": "x"}, headers=headers)

    assert hours.status_code == 200 and "days" in hours.json()
    assert business.status_code == 200 and business.json()["id"] == str(tenant_a.business_id)
    assert patch.status_code == 200 and patch.json()["name"] == "Still works"
    assert patch_hours.status_code == 422  # "hours" is not a UUID: never routed to hours


async def test_every_route_is_402_without_a_plan_and_401_without_a_token(client, two_tenants):
    tenant, _ = two_tenants
    for method, body in (("GET", None), ("PUT", _week())):
        response = await client.request(
            method, "/api/v1/business/hours", json=body, headers=_auth(tenant)
        )
        assert response.status_code == 402
        assert response.json()["error"]["code"] == "subscription_required"
        assert (
            await client.request(method, "/api/v1/business/hours", json=body)
        ).status_code == 401


def _without(week: dict, day: int) -> dict:
    return {"days": [d for d in week["days"] if d["day_of_week"] != day]}


def _bad_weeks() -> list[dict]:
    full = _week()
    duplicate = {"days": [*full["days"][:6], full["days"][0]]}
    closed_with_times = _week()
    closed_with_times["days"][0]["opens_at"] = "09:00"
    inverted = _week("17:00", "09:00")
    equal = _week("09:00", "09:00")
    open_without_times = _week()
    open_without_times["days"][1] = {"day_of_week": 1, "is_closed": False}
    return [
        _without(full, 6),
        {"days": [*full["days"], {"day_of_week": 0, "is_closed": True}]},
        duplicate,
        closed_with_times,
        inverted,
        equal,
        open_without_times,
        {"days": [{**d, "day_of_week": 7} if d["day_of_week"] == 6 else d for d in full["days"]]},
        {**full, "business_id": str(uuid.uuid4())},
    ]


async def test_invalid_weeks_are_422_and_change_nothing(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    headers = _auth(tenant_a)
    good = await client.put("/api/v1/business/hours", json=_week(), headers=headers)
    assert good.status_code == 200

    for payload in _bad_weeks():
        response = await client.put("/api/v1/business/hours", json=payload, headers=headers)
        assert response.status_code == 422, payload
        assert response.json()["error"]["code"] == "validation_error"

    assert (await client.get("/api/v1/business/hours", headers=headers)).json() == good.json()
