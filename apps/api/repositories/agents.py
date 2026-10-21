"""Repository for `agents` (one persona row per business).

Every query is scoped by `business_id` from `TenantContext`. Get-or-create
is `INSERT ... ON CONFLICT (business_id) DO NOTHING` followed by a select,
so two first requests racing for one business both end up with the same row.
"""

import uuid

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.agent import Agent
from repositories.base import business_scoped_select


async def get_agent(session: AsyncSession, business_id: uuid.UUID) -> Agent | None:
    result = await session.execute(business_scoped_select(Agent, business_id))
    return result.scalar_one_or_none()


async def get_or_create_agent(
    session: AsyncSession, business_id: uuid.UUID, *, name: str, tone: str, greeting: str
) -> Agent:
    await session.execute(
        pg_insert(Agent)
        .values(id=uuid.uuid4(), business_id=business_id, name=name, tone=tone, greeting=greeting)
        .on_conflict_do_nothing(index_elements=[Agent.business_id])
    )
    agent = await get_agent(session, business_id)
    assert agent is not None
    return agent


async def save_agent(session: AsyncSession, agent: Agent) -> Agent:
    """Flushes edits, then reloads the server-computed `updated_at`."""
    await session.flush()
    await session.refresh(agent)
    return agent


class SqlAgentStore:
    """Binds the functions above to one session, matching `services.agent.AgentStore`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(
        self, business_id: uuid.UUID, *, name: str, tone: str, greeting: str
    ) -> Agent:
        return await get_or_create_agent(
            self._session, business_id, name=name, tone=tone, greeting=greeting
        )

    async def save(self, agent: Agent) -> Agent:
        return await save_agent(self._session, agent)
