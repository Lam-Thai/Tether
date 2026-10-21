"""Dependencies that build the `ConversationEngine` and the agent store for a request."""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db import get_session
from core.llm import get_llm_client
from core.usage import get_usage_guard
from integrations.llm.port import LLMClient
from repositories.agents import SqlAgentStore
from repositories.interactions import SqlConversationStore
from services.conversation import ConversationEngine
from services.tools import ToolRegistry
from services.usage import UsageGuard


def get_agent_store(session: AsyncSession = Depends(get_session)) -> SqlAgentStore:
    return SqlAgentStore(session)


def get_conversation_engine(
    session: AsyncSession = Depends(get_session),
    llm: LLMClient = Depends(get_llm_client),
    usage: UsageGuard = Depends(get_usage_guard),
) -> ConversationEngine:
    return ConversationEngine(
        store=_ConversationStore(session),
        llm=llm,
        tools=ToolRegistry(),  # empty until Slice 6 registers the first tool
        history_limit=settings.conversation_history_limit,
        turn_timeout_seconds=settings.conversation_turn_timeout_seconds,
        usage=usage,
    )


class _ConversationStore(SqlConversationStore, SqlAgentStore):
    """One session-bound store with both the conversation and the agent operations."""

    def __init__(self, session: AsyncSession) -> None:
        SqlConversationStore.__init__(self, session)
        SqlAgentStore.__init__(self, session)
