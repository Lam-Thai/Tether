"""`/api/v1/business/booking-rules` against a real Postgres."""

import pytest
from httpx import ASGITransport, AsyncClient

from core.booking_rules import default_booking_rules
from services.booking_rules import BookingRules

URL = "/api/v1/business/booking-rules"
BODY = {
    "slot_interval_minutes": 30,
    "min_notice_minutes": 0,
    "max_days_ahead": 14,
    "buffer_minutes": 10,
}


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_defaults_when_no_row(client, entitled_tenants, app):
    tenant_a, _ = entitled_tenants
    app.dependency_overrides[default_booking_rules] = lambda: BookingRules(15, 120, 60, 0)
    try:
        response = await client.get(URL, headers=_auth(tenant_a))
    finally:
        app.dependency_overrides.pop(default_booking_rules, None)
    assert response.status_code == 200
    assert response.json() == {
        "slot_interval_minutes": 15,
        "min_notice_minutes": 120,
        "max_days_ahead": 60,
        "buffer_minutes": 0,
        "customized": False,
    }


async def test_put_get_overwrite_and_cross_tenant(client, entitled_tenants):
    tenant_a, tenant_b = entitled_tenants

    saved = await client.put(URL, json=BODY, headers=_auth(tenant_a))
    assert saved.status_code == 200, saved.text
    assert saved.json() == {**BODY, "customized": True}
    assert (await client.get(URL, headers=_auth(tenant_a))).json() == saved.json()

    again = await client.put(URL, json={**BODY, "buffer_minutes": 0}, headers=_auth(tenant_a))
    assert again.json()["buffer_minutes"] == 0

    # B never sees A's rules, and B's own PUT leaves A untouched.
    b_before = (await client.get(URL, headers=_auth(tenant_b))).json()
    assert b_before["customized"] is False
    b_put = await client.put(
        URL,
        json={**BODY, "slot_interval_minutes": 60, "max_days_ahead": 7},
        headers=_auth(tenant_b),
    )
    assert b_put.status_code == 200 and b_put.json()["slot_interval_minutes"] == 60
    a_after = (await client.get(URL, headers=_auth(tenant_a))).json()
    assert a_after == again.json()


@pytest.mark.parametrize(
    "change",
    [
        {"slot_interval_minutes": 7},
        {"slot_interval_minutes": 0},
        {"slot_interval_minutes": 245},
        {"min_notice_minutes": -1},
        {"min_notice_minutes": 20161},
        {"max_days_ahead": 0},
        {"max_days_ahead": 366},
        {"buffer_minutes": -5},
        {"buffer_minutes": 241},
        {"buffer_minutes": "ten"},
    ],
    ids=lambda c: "-".join(f"{k}={v}" for k, v in c.items()),
)
async def test_put_rejects_out_of_range(client, entitled_tenants, change):
    tenant_a, _ = entitled_tenants
    response = await client.put(URL, json={**BODY, **change}, headers=_auth(tenant_a))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert (await client.get(URL, headers=_auth(tenant_a))).json()["customized"] is False


async def test_put_requires_all_fields_and_forbids_extras(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    missing = {k: v for k, v in BODY.items() if k != "buffer_minutes"}
    assert (await client.put(URL, json=missing, headers=_auth(tenant_a))).status_code == 422
    other = await client.put(
        URL, json={**BODY, "business_id": str(tenant_a.business_id)}, headers=_auth(tenant_a)
    )
    assert other.status_code == 422


async def test_every_route_is_402_without_a_plan_and_401_without_a_token(client, two_tenants):
    tenant_a, _ = two_tenants
    got = await client.get(URL, headers=_auth(tenant_a))
    put = await client.put(URL, json=BODY, headers=_auth(tenant_a))
    assert got.status_code == 402 and put.status_code == 402
    assert got.json()["error"]["code"] == "subscription_required"
    assert (await client.get(URL)).status_code == 401
    assert (await client.put(URL, json=BODY)).status_code == 401
