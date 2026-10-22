"""The operation log against real Postgres: round trip, ordering, and business scoping."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import async_session_factory
from repositories import interactions as interactions_repo
from repositories import operations as repo


async def _interaction(business_id: uuid.UUID) -> uuid.UUID:
    async with async_session_factory() as session:
        interaction = await interactions_repo.create_interaction(session, business_id, "test_chat")
        await session.commit()
        return interaction.id


async def test_records_round_trip_in_order_and_never_cross_businesses(two_tenants):
    tenant_a, tenant_b = two_tenants
    interaction_id = await _interaction(tenant_a.business_id)
    faq_id = str(uuid.uuid4())

    async with async_session_factory() as session:
        log = repo.SqlOperationLog(session)
        await log.record_operation(
            business_id=tenant_a.business_id,
            interaction_id=interaction_id,
            tool_name="search_faq",
            arguments={"query": "walk in"},
            status="succeeded",
            result={"faq_ids": [faq_id], "count": 1},
            duration_ms=12,
        )
        await log.record_operation(
            business_id=tenant_a.business_id,
            interaction_id=interaction_id,
            tool_name="search_faq",
            arguments={"argument_names": ["q"]},
            status="rejected",
            result={"reason": "invalid_arguments", "fields": ["query"]},
            duration_ms=0,
        )
        await session.commit()

    async with async_session_factory() as session:
        mine = await repo.list_operations(session, tenant_a.business_id, interaction_id)
        theirs = await repo.list_operations(session, tenant_b.business_id, interaction_id)

    assert [(op.tool_name, op.status) for op in mine] == [
        ("search_faq", "succeeded"),
        ("search_faq", "rejected"),
    ]
    assert mine[0].arguments == {"query": "walk in"}
    assert mine[0].result == {"faq_ids": [faq_id], "count": 1}
    assert mine[0].duration_ms == 12 and mine[0].created_at is not None
    assert list(theirs) == []


async def test_unknown_status_is_refused_by_the_table(two_tenants):
    tenant_a, _ = two_tenants
    interaction_id = await _interaction(tenant_a.business_id)

    async with async_session_factory() as session:
        with pytest.raises(IntegrityError):
            await repo.record_operation(
                session,
                business_id=tenant_a.business_id,
                interaction_id=interaction_id,
                tool_name="search_faq",
                arguments={},
                status="maybe",
                result={},
                duration_ms=1,
            )
        await session.rollback()
