"""Repository for `interaction_operations` (the operation log).

Append-only: one row per tool call, written by `services/tools.ToolExecutor`
through `SqlOperationLog`. Rows are flushed, not committed: the engine's
`checkpoint()` commits them before the next model call, so a logged action is
durable before the model is told about it. Every read is scoped by
`business_id` from `TenantContext`.
"""

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from models.operation import InteractionOperation
from repositories.base import business_scoped_select


async def record_operation(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    interaction_id: uuid.UUID,
    tool_name: str,
    arguments: Mapping[str, Any],
    status: str,
    result: Mapping[str, Any],
    duration_ms: int,
) -> InteractionOperation:
    operation = InteractionOperation(
        id=uuid.uuid4(),
        business_id=business_id,
        interaction_id=interaction_id,
        tool_name=tool_name,
        arguments=dict(arguments),
        status=status,
        result=dict(result),
        duration_ms=duration_ms,
    )
    session.add(operation)
    await session.flush()
    return operation


async def list_operations(
    session: AsyncSession, business_id: uuid.UUID, interaction_id: uuid.UUID
) -> Sequence[InteractionOperation]:
    """Oldest first."""
    result = await session.execute(
        business_scoped_select(InteractionOperation, business_id)
        .where(InteractionOperation.interaction_id == interaction_id)
        .order_by(InteractionOperation.created_at, InteractionOperation.id)
    )
    return result.scalars().all()


class SqlOperationLog:
    """Binds `record_operation` to one session (`services.tools.OperationLog`)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_operation(
        self,
        *,
        business_id: uuid.UUID,
        interaction_id: uuid.UUID,
        tool_name: str,
        arguments: Mapping[str, Any],
        status: str,
        result: Mapping[str, Any],
        duration_ms: int,
    ) -> None:
        await record_operation(
            self._session,
            business_id=business_id,
            interaction_id=interaction_id,
            tool_name=tool_name,
            arguments=arguments,
            status=status,
            result=result,
            duration_ms=duration_ms,
        )
