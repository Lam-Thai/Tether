"""`/api/v1/agent` — the owner's receptionist persona and its test chat.

Router responsibility only: parse, call one service method, commit, shape the
response. Every route depends on `get_entitled_tenant` (402 without a plan);
the business always comes from the tenant, never from the request.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import get_entitled_tenant
from core.conversation import get_agent_store, get_conversation_engine
from core.db import get_session
from core.tenant import Tenant
from repositories.agents import SqlAgentStore
from schemas.agent import (
    AgentRead,
    AgentUpdate,
    TestTurnCreate,
    TranscriptRead,
    TurnRead,
)
from services import agent as agent_service
from services.conversation import ConversationEngine

router = APIRouter(tags=["agent"])

Entitled = Annotated[Tenant, Depends(get_entitled_tenant)]
AgentStore = Annotated[SqlAgentStore, Depends(get_agent_store)]
Engine = Annotated[ConversationEngine, Depends(get_conversation_engine)]
Session = Annotated[AsyncSession, Depends(get_session)]

CHANNEL = "test_chat"


@router.get("/agent", response_model=AgentRead)
async def get_agent(tenant: Entitled, store: AgentStore, session: Session) -> AgentRead:
    agent = await agent_service.get_agent(store, tenant)
    await session.commit()  # persists the defaults on first read
    return AgentRead.model_validate(agent)


@router.patch("/agent", response_model=AgentRead)
async def patch_agent(
    body: AgentUpdate, tenant: Entitled, store: AgentStore, session: Session
) -> AgentRead:
    agent = await agent_service.update_agent(store, tenant, **body.model_dump(exclude_unset=True))
    await session.commit()
    return AgentRead.model_validate(agent)


@router.post("/agent/test", response_model=TurnRead)
async def post_test_turn(
    body: TestTurnCreate, tenant: Entitled, engine: Engine, session: Session
) -> TurnRead:
    result = await engine.handle_turn(tenant, body.interaction_id, body.message, CHANNEL)
    await session.commit()
    return TurnRead.model_validate(result)


@router.get("/agent/test/{interaction_id}", response_model=TranscriptRead)
async def get_test_transcript(
    interaction_id: UUID, tenant: Entitled, engine: Engine
) -> TranscriptRead:
    return TranscriptRead.model_validate(await engine.transcript(tenant, interaction_id, CHANNEL))


@router.post("/agent/test/{interaction_id}/end", response_model=TranscriptRead)
async def post_end_test_chat(
    interaction_id: UUID, tenant: Entitled, engine: Engine, session: Session
) -> TranscriptRead:
    transcript = await engine.end(tenant, interaction_id, CHANNEL)
    await session.commit()
    return TranscriptRead.model_validate(transcript)
