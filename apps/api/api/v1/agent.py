"""`/api/v1/agent` — the owner's receptionist persona, its voice and its test chat.

Router responsibility only: parse, call one service method, commit, shape the
response. Every route depends on `get_entitled_tenant` (402 without a plan);
the business always comes from the tenant, never from the request.

Voice (slice 15): `GET /agent/voices` lists the settings-driven voices; `PATCH /agent`
takes `voice`; `POST /agent/voice-preview` returns the saved greeting as `audio/wav`
(a metered `tts_request`: 429 `usage_limit_reached` when the day's limit is reached).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import get_entitled_tenant
from core.conversation import get_agent_store, get_conversation_engine
from core.db import get_session
from core.rate_limit import limit_agent_test, limit_voice_preview
from core.readiness import get_agent_activation
from core.speech import get_speech_synthesizer, get_voice_catalog
from core.tenant import Tenant
from models.agent import Agent
from repositories.agents import SqlAgentStore
from schemas.agent import (
    AgentRead,
    AgentUpdate,
    TestTurnCreate,
    TranscriptRead,
    TurnRead,
    VoicePreviewCreate,
    VoiceRead,
    VoicesRead,
)
from schemas.dashboard import ReadinessRead
from services import agent as agent_service
from services.conversation import ConversationEngine
from services.readiness import AgentActivation
from services.speech import SpeechSynthesizer, VoiceCatalog

router = APIRouter(tags=["agent"])

Entitled = Annotated[Tenant, Depends(get_entitled_tenant)]
AgentStore = Annotated[SqlAgentStore, Depends(get_agent_store)]
Engine = Annotated[ConversationEngine, Depends(get_conversation_engine)]
Session = Annotated[AsyncSession, Depends(get_session)]
Catalog = Annotated[VoiceCatalog, Depends(get_voice_catalog)]
Speech = Annotated[SpeechSynthesizer, Depends(get_speech_synthesizer)]
Activation = Annotated[AgentActivation, Depends(get_agent_activation)]

CHANNEL = "test_chat"


def _agent_read(agent: Agent, catalog: VoiceCatalog) -> AgentRead:
    fields = {name: getattr(agent, name) for name in AgentRead.model_fields if name != "voice"}
    return AgentRead(**fields, voice=catalog.effective(agent.voice))


@router.get("/agent", response_model=AgentRead)
async def get_agent(
    tenant: Entitled, store: AgentStore, session: Session, catalog: Catalog
) -> AgentRead:
    agent = await agent_service.get_agent(store, tenant)
    await session.commit()  # persists the defaults on first read
    return _agent_read(agent, catalog)


@router.patch("/agent", response_model=AgentRead)
async def patch_agent(
    body: AgentUpdate, tenant: Entitled, store: AgentStore, session: Session, catalog: Catalog
) -> AgentRead:
    agent = await agent_service.update_agent(
        store, tenant, catalog=catalog, **body.model_dump(exclude_unset=True)
    )
    await session.commit()
    return _agent_read(agent, catalog)


@router.get("/agent/readiness", response_model=ReadinessRead)
async def get_readiness(
    tenant: Entitled, activation: Activation, session: Session
) -> ReadinessRead:
    state = await activation.state(tenant)
    await session.commit()  # the default agent row, a calendar probe's result
    return ReadinessRead.from_activation(state)


@router.post("/agent/activate", response_model=ReadinessRead)
async def post_activate(
    tenant: Entitled, activation: Activation, session: Session
) -> ReadinessRead:
    """Flips `active` only when every required checklist item passes (else 409
    `agent_not_ready`); idempotent."""
    state = await activation.activate(tenant)
    await session.commit()
    return ReadinessRead.from_activation(state)


@router.post("/agent/deactivate", response_model=ReadinessRead)
async def post_deactivate(
    tenant: Entitled, activation: Activation, session: Session
) -> ReadinessRead:
    state = await activation.deactivate(tenant)
    await session.commit()
    return ReadinessRead.from_activation(state)


@router.get("/agent/voices", response_model=VoicesRead)
async def get_voices(tenant: Entitled, catalog: Catalog, speech: Speech) -> VoicesRead:
    return VoicesRead(
        voices=[VoiceRead.model_validate(v) for v in catalog.options()],
        default_voice=catalog.default,
        tts_configured=speech.configured,
    )


@router.post(
    "/agent/voice-preview",
    dependencies=[Depends(limit_voice_preview)],
    response_class=Response,
    responses={200: {"content": {"audio/wav": {}}}},
)
async def post_voice_preview(
    body: VoicePreviewCreate, tenant: Entitled, store: AgentStore, session: Session, speech: Speech
) -> Response:
    audio = await agent_service.preview_voice(
        store, speech, tenant, body.voice, checkpoint=session.commit
    )
    return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.post("/agent/test", response_model=TurnRead, dependencies=[Depends(limit_agent_test)])
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


@router.post(
    "/agent/test/{interaction_id}/end",
    response_model=TranscriptRead,
    dependencies=[Depends(limit_agent_test)],
)
async def post_end_test_chat(
    interaction_id: UUID, tenant: Entitled, engine: Engine, session: Session
) -> TranscriptRead:
    transcript = await engine.end(tenant, interaction_id, CHANNEL)
    await session.commit()
    return TranscriptRead.model_validate(transcript)
