"""The inbox seed against real Postgres: idempotent, and the inbox stays fast on it."""

import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from core.db import async_session_factory
from models.business import Business
from models.interaction import Interaction, InteractionMessage
from seeds.demo_inbox import DEFAULT_COUNT, add_interactions

BUDGET_SECONDS = 1.0


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _seed(business_id) -> tuple[int, int]:
    async with async_session_factory() as session:
        business = (
            await session.execute(select(Business).where(Business.id == business_id))
        ).scalar_one()
        result = await add_interactions(session, business)
        await session.commit()
        return result


async def _count(model, business_id) -> int:
    async with async_session_factory() as session:
        found = await session.scalar(
            select(func.count()).select_from(model).where(model.business_id == business_id)
        )
    return int(found or 0)


async def test_seed_is_idempotent_and_stays_in_its_business(entitled_tenants):
    tenant_a, tenant_b = entitled_tenants

    first = await _seed(tenant_a.business_id)
    second = await _seed(tenant_a.business_id)

    assert first == (DEFAULT_COUNT, 0) and second == (0, DEFAULT_COUNT)
    assert await _count(Interaction, tenant_a.business_id) == DEFAULT_COUNT
    assert await _count(Interaction, tenant_b.business_id) == 0
    assert await _count(InteractionMessage, tenant_a.business_id) > DEFAULT_COUNT * 3


async def _timed(client, url, tenant, **params):
    started = time.perf_counter()
    response = await client.get(url, params=params, headers=_auth(tenant))
    elapsed = time.perf_counter() - started
    assert response.status_code == 200, response.text
    return response.json(), elapsed


async def test_list_detail_and_messages_each_answer_within_a_second_on_seeded_data(
    client, entitled_tenants
):
    tenant_a, _ = entitled_tenants
    await _seed(tenant_a.business_id)

    page, listed = await _timed(
        client, "/api/v1/interactions", tenant_a, type="bookings", status="completed", limit=50
    )
    everything, unfiltered = await _timed(client, "/api/v1/interactions", tenant_a, limit=100)
    first_id = page["items"][0]["id"]
    detail, detailed = await _timed(client, f"/api/v1/interactions/{first_id}", tenant_a)
    messages, transcript = await _timed(
        client, f"/api/v1/interactions/{first_id}/messages", tenant_a
    )

    assert page["items"] and all(i["status"] == "completed" for i in page["items"])
    assert len(everything["items"]) == 100 and everything["next_cursor"]
    assert detail["operations"] is not None and messages["items"]
    for elapsed in (listed, unfiltered, detailed, transcript):
        assert elapsed < BUDGET_SECONDS
