"""Repository-level checks for the parts where scoping and locking are not trivial."""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from core.db import async_session_factory
from repositories import agents as agents_repo
from repositories import interactions as repo


async def test_agent_get_or_create_is_race_safe_and_scoped(two_tenants):
    tenant_a, tenant_b = two_tenants

    async def _create(business_id: uuid.UUID, name: str) -> uuid.UUID:
        async with async_session_factory() as session:
            agent = await agents_repo.get_or_create_agent(
                session, business_id, name=name, tone="friendly", greeting="Hi"
            )
            await session.commit()
            return agent.id

    ids = await asyncio.gather(*[_create(tenant_a.business_id, f"racer {i}") for i in range(4)])
    other = await _create(tenant_b.business_id, "b")

    assert len(set(ids)) == 1  # one row, however many first requests raced
    assert other != ids[0]
    async with async_session_factory() as session:
        assert await agents_repo.get_agent(session, uuid.uuid4()) is None


async def test_reads_and_locks_never_cross_businesses(two_tenants):
    tenant_a, tenant_b = two_tenants
    async with async_session_factory() as session:
        interaction = await repo.create_interaction(session, tenant_a.business_id, "test_chat")
        await repo.append_message(session, interaction, "agent", "hello")
        await session.commit()
        interaction_id = interaction.id

    async with async_session_factory() as session:
        mine = await repo.get_interaction(
            session, tenant_a.business_id, interaction_id, "test_chat"
        )
        theirs = await repo.get_interaction(
            session, tenant_b.business_id, interaction_id, "test_chat"
        )
        wrong_channel = await repo.get_interaction(
            session, tenant_a.business_id, interaction_id, "voice"
        )
        locked_by_b = await repo.lock_interaction(
            session, tenant_b.business_id, interaction_id, "test_chat"
        )
        messages_for_b = await repo.list_messages(session, tenant_b.business_id, interaction_id)
        messages_for_a = await repo.list_messages(session, tenant_a.business_id, interaction_id)

    assert mine is not None
    assert theirs is None and wrong_channel is None and locked_by_b is None
    assert list(messages_for_b) == []
    assert [m.message_text for m in messages_for_a] == ["hello"]


async def test_list_messages_limit_returns_the_latest_in_order(two_tenants):
    tenant_a, _ = two_tenants
    async with async_session_factory() as session:
        interaction = await repo.create_interaction(session, tenant_a.business_id, "test_chat")
        for i in range(5):
            await repo.append_message(session, interaction, "customer", f"m{i}")
        await session.commit()

        recent = await repo.list_messages(session, tenant_a.business_id, interaction.id, limit=2)

    assert [(m.sequence_number, m.message_text) for m in recent] == [(4, "m3"), (5, "m4")]


async def test_a_second_locker_gets_interaction_busy_not_a_hang(two_tenants, monkeypatch):
    tenant_a, _ = two_tenants
    monkeypatch.setattr(repo, "LOCK_TIMEOUT", "200ms")
    async with async_session_factory() as session:
        interaction = await repo.create_interaction(session, tenant_a.business_id, "test_chat")
        await session.commit()
        interaction_id = interaction.id

    async with async_session_factory() as holder, async_session_factory() as waiter:
        held = await repo.lock_interaction(
            holder, tenant_a.business_id, interaction_id, "test_chat"
        )
        assert held is not None
        with pytest.raises(repo.InteractionBusyError):
            await repo.lock_interaction(waiter, tenant_a.business_id, interaction_id, "test_chat")
        await holder.rollback()
        await waiter.rollback()


async def test_a_failed_savepoint_leaves_the_session_usable(two_tenants):
    """What lets `ToolExecutor` log a tool that died on a DB error."""
    tenant, _ = two_tenants
    async with async_session_factory() as session:
        store = repo.SqlConversationStore(session)
        interaction = await store.create_interaction(tenant.business_id, "test_chat")
        with pytest.raises(DBAPIError):
            async with store.savepoint():
                await session.execute(text("SELECT 1 / 0"))

        await store.append_message(interaction, "agent", "still writable")
        await store.checkpoint()

    async with async_session_factory() as session:
        rows = await repo.list_messages(session, tenant.business_id, interaction.id)
    assert [r.message_text for r in rows] == ["still writable"]
