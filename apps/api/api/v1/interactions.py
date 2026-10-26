"""`/api/v1/interactions` — the owner's inbox: list, detail, transcript.

Router responsibility only: parse the query, call one `Inbox` method, commit what the
read wrote (the idle sweep), shape the response. Every route depends on
`get_entitled_tenant`; the business always comes from the tenant, so another
business's interaction is a 404.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import get_entitled_tenant
from core.db import get_session
from core.inbox import get_inbox
from core.tenant import Tenant
from schemas.agent_turn import Intent
from schemas.inbox import (
    InteractionDetailRead,
    InteractionPageRead,
    InteractionRead,
    InteractionStatus,
    MessagePageRead,
    OperationRead,
)
from services.inbox import Inbox, InboxFilters, TypeFilter

router = APIRouter(tags=["inbox"])

Entitled = Annotated[Tenant, Depends(get_entitled_tenant)]
InboxService = Annotated[Inbox, Depends(get_inbox)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/interactions", response_model=InteractionPageRead)
async def list_interactions(
    tenant: Entitled,
    inbox: InboxService,
    session: Session,
    type: TypeFilter = "all",
    status: InteractionStatus | None = None,
    intent: Intent | None = None,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> InteractionPageRead:
    filters = InboxFilters(
        type=type, status=status, intent=intent, from_date=from_date, to_date=to_date
    )
    result = await inbox.list(tenant, filters, cursor, limit)
    await session.commit()
    return InteractionPageRead(
        items=[InteractionRead.model_validate(row) for row in result.items],
        next_cursor=result.next_cursor,
        timezone=result.timezone,
    )


@router.get("/interactions/{interaction_id}", response_model=InteractionDetailRead)
async def get_interaction(
    interaction_id: UUID, tenant: Entitled, inbox: InboxService, session: Session
) -> InteractionDetailRead:
    view = await inbox.detail(tenant, interaction_id)
    await session.commit()
    row = InteractionRead.model_validate(view.detail.row)
    return InteractionDetailRead(
        **row.model_dump(),
        timezone=view.timezone,
        message_count=view.detail.message_count,
        operations=[OperationRead.model_validate(op) for op in view.detail.operations],
    )


@router.get("/interactions/{interaction_id}/messages", response_model=MessagePageRead)
async def list_interaction_messages(
    interaction_id: UUID,
    tenant: Entitled,
    inbox: InboxService,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> MessagePageRead:
    return MessagePageRead.model_validate(
        await inbox.messages(tenant, interaction_id, after, limit)
    )
