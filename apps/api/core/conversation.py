"""Dependencies that build the `ConversationEngine` and the agent store for a request."""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.booking import build_booking_service
from core.booking_rules import default_booking_rules
from core.calendar import (
    get_calendar_api,
    get_calendar_config,
    get_clock,
    get_oauth_state_signer,
    get_token_vault,
)
from core.config import settings
from core.db import get_session
from core.directory import SqlDirectoryStore
from core.llm import get_llm_client
from core.summaries import build_summarizer
from core.usage import get_usage_guard
from integrations.google_calendar import GoogleCalendarApi
from integrations.llm.port import LLMClient
from repositories.agents import SqlAgentStore
from repositories.bookings import SqlBookingStore
from repositories.calendar import SqlCalendarStore
from repositories.faqs import SqlFaqStore
from repositories.interactions import SqlConversationStore
from repositories.operations import SqlOperationLog
from services.availability import AvailabilityService
from services.availability_tool import check_availability_tool
from services.booking_rules import BookingRules
from services.booking_tool import create_booking_tool
from services.business_tools import get_business_info_tool, get_services_tool
from services.calendar import CalendarConfig, CalendarConnections, GoogleCalendarGateway
from services.conversation import ConversationEngine
from services.directory import BusinessDirectory
from services.faq_search import search_faq_tool
from services.knowledge import KnowledgeBase
from services.lifecycle import InteractionLifecycle, NoSummarizer
from services.oauth_state import OAuthStateSigner
from services.summarizer import SummarizerPort
from services.token_vault import TokenVault
from services.tools import ToolRegistry
from services.usage import UsageGuard


def get_agent_store(session: AsyncSession = Depends(get_session)) -> SqlAgentStore:
    return SqlAgentStore(session)


def get_conversation_engine(
    session: AsyncSession = Depends(get_session),
    llm: LLMClient = Depends(get_llm_client),
    usage: UsageGuard = Depends(get_usage_guard),
    calendar_api: GoogleCalendarApi = Depends(get_calendar_api),
    vault: TokenVault = Depends(get_token_vault),
    signer: OAuthStateSigner = Depends(get_oauth_state_signer),
    calendar_config: CalendarConfig = Depends(get_calendar_config),
    booking_defaults: BookingRules = Depends(default_booking_rules),
    clock: Callable[[], datetime] = Depends(get_clock),
) -> ConversationEngine:
    return build_conversation_engine(
        session,
        llm,
        usage,
        clock=clock,
        calendar=CalendarDeps(calendar_api, vault, signer, calendar_config),
        booking_defaults=booking_defaults,
        # Ending a conversation writes its summary (a metered model call, after the close).
        summarizer=build_summarizer(session, llm, usage, clock=clock),
    )


class CalendarDeps:
    """What availability and booking need to reach Google Calendar for this business."""

    def __init__(
        self,
        api: GoogleCalendarApi,
        vault: TokenVault,
        signer: OAuthStateSigner,
        config: CalendarConfig,
    ) -> None:
        self.api = api
        self.vault = vault
        self.signer = signer
        self.config = config

    @classmethod
    def from_process(cls) -> "CalendarDeps":
        """The process-wide ones (unconfigured placeholders until the feature is set up)."""
        return cls(
            get_calendar_api(), get_token_vault(), get_oauth_state_signer(), get_calendar_config()
        )

    def connections(self, session: AsyncSession, usage: UsageGuard) -> CalendarConnections:
        return CalendarConnections(
            store=SqlCalendarStore(session),
            gateway=GoogleCalendarGateway(self.api, self.vault, usage),
            api=self.api,
            vault=self.vault,
            signer=self.signer,
            usage=usage,
            config=self.config,
        )


def build_conversation_engine(
    session: AsyncSession,
    llm: LLMClient,
    usage: UsageGuard,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    extra_turn_seconds: float = 0.0,
    calendar: CalendarDeps | None = None,
    booking_defaults: BookingRules | None = None,
    summarizer: SummarizerPort | None = None,
) -> ConversationEngine:
    """The production engine and tool registry over one session. The eval harness
    (`evals/sandbox.py`) builds the same one with a pinned business clock, a fake
    calendar, and extra turn time for its own request pacing. Without a `summarizer`
    ending a conversation closes it with no summary and makes no model call."""
    store = _ConversationStore(session)
    # One directory for the prompt's facts and the tools, so both read the same rows.
    directory = BusinessDirectory(
        SqlDirectoryStore(session), clock, booking_defaults or default_booking_rules()
    )
    calendar = calendar or CalendarDeps.from_process()
    connections = calendar.connections(session, usage)
    availability = AvailabilityService(directory, connections, savepoint=store.savepoint)
    bookings = build_booking_service(directory, connections, SqlBookingStore(session))
    return ConversationEngine(
        store=store,
        llm=llm,
        # Bound to the request's session, like the store: tool reads and op-log
        # rows are committed by the engine's checkpoint before each model call.
        tools=ToolRegistry.of(
            [
                search_faq_tool(KnowledgeBase(SqlFaqStore(session))),
                get_services_tool(directory),
                get_business_info_tool(directory),
                check_availability_tool(directory, availability, savepoint=store.savepoint),
                create_booking_tool(directory, bookings, savepoint=store.savepoint),
            ]
        ),
        history_limit=settings.conversation_history_limit,
        turn_timeout_seconds=settings.conversation_turn_timeout_seconds + extra_turn_seconds,
        usage=usage,
        directory=directory,
        lifecycle=InteractionLifecycle(
            store,
            summarizer or NoSummarizer(),
            idle_minutes=usage.interaction_idle_minutes,
        ),
    )


class _ConversationStore(SqlConversationStore, SqlAgentStore, SqlOperationLog):
    """One session-bound store with the conversation, agent and operation-log operations."""

    def __init__(self, session: AsyncSession) -> None:
        SqlConversationStore.__init__(self, session)
        SqlAgentStore.__init__(self, session)
        SqlOperationLog.__init__(self, session)
