"""A new agent row is inactive at the database level (slice 16's `server_default`), so any
insert path, not just the ORM default, starts a business off the phones."""

import uuid

from sqlalchemy import text

from core.db import async_session_factory


async def test_an_agent_inserted_without_active_is_inactive(two_tenants):
    tenant_a, _ = two_tenants
    async with async_session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO agents (id, business_id, name, tone, greeting) "
                "VALUES (:id, :b, 'Riley', 'friendly', 'Hi')"
            ),
            {"id": uuid.uuid4(), "b": tenant_a.business_id},
        )
        active = await session.scalar(
            text("SELECT active FROM agents WHERE business_id = :b"), {"b": tenant_a.business_id}
        )
        await session.commit()

    assert active is False
