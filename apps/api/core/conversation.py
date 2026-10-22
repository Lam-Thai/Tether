"""Dependencies that build the `ConversationEngine` and the agent store for a request."""

from datetime import UTC, datetime

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db import get_session
from core.directory import SqlDirectoryStore
from core.llm import get_llm_client
from core.usage import get_usage_guard
from integrations.llm.port import LLMClient
from repositories.agents import SqlAgentStore
from repositories.faqs import SqlFaqStore
from repositories.interactions import SqlConversationStore
from repositories.operations import SqlOperationLog
from services.business_tools import get_business_info_tool, get_services_tool
from services.conversation import ConversationEngine
from services.directory import BusinessDirectory
from services.faq_search import search_faq_tool
from services.knowledge import KnowledgeBase
from services.tools import ToolRegistry
from services.usage import UsageGuard


def get_agent_store(session: AsyncSession = Depends(get_session)) -> SqlAgentStore:
    return SqlAgentStore(session)


def get_conversation_engine(
    session: AsyncSession = Depends(get_session),
    llm: LLMClient = Depends(get_llm_client),
    usage: UsageGuard = Depends(get_usage_guard),
) -> ConversationEngine:
    # One directory for the prompt's facts and the tools, so both read the same rows.
    directory = BusinessDirectory(SqlDirectoryStore(session), lambda: datetime.now(UTC))
    return ConversationEngine(
        store=_ConversationStore(session),
        llm=llm,
        # Bound to the request's session, like the store: tool reads and op-log
        # rows are committed by the engine's checkpoint before each model call.
        tools=ToolRegistry.of(
            [
                search_faq_tool(KnowledgeBase(SqlFaqStore(session))),
                get_services_tool(directory),
                get_business_info_tool(directory),
            ]
        ),
        history_limit=settings.conversation_history_limit,
        turn_timeout_seconds=settings.conversation_turn_timeout_seconds,
        usage=usage,
        directory=directory,
    )


class _ConversationStore(SqlConversationStore, SqlAgentStore, SqlOperationLog):
    """One session-bound store with the conversation, agent and operation-log operations."""

    def __init__(self, session: AsyncSession) -> None:
        SqlConversationStore.__init__(self, session)
        SqlAgentStore.__init__(self, session)
        SqlOperationLog.__init__(self, session)
