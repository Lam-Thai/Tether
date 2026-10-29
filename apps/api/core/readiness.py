"""Dependencies for the activation checklist, the integration status, the dashboard
numbers and "delete test data" (slice 16).

The SQL stores live here, not in `repositories/`, because they speak the services'
dataclasses; the SQL itself is in `repositories/readiness.py`, `dashboard.py` and
`test_data.py`. One request builds one calendar-status lookup (`CalendarStatusOnce`), shared
by the readiness check and the integration status, so a page that needs both probes Google
at most once.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.calendar import get_calendar_config, get_calendar_connections, get_clock
from core.conversation import get_agent_store
from core.db import get_session
from core.usage import get_usage_guard
from core.voice import get_webhook_targets
from repositories import dashboard as dashboard_repo
from repositories import readiness as repo
from repositories import test_data as test_data_repo
from repositories.agents import SqlAgentStore, get_agent
from services.agent import DEFAULT_NAME
from services.calendar import CalendarConfig, CalendarConnections, CalendarStatus
from services.dashboard import DashboardMetrics, MetricCounts
from services.integration_status import IntegrationStatus, PhoneRecord, SmsFailure
from services.phone_numbers import WebhookTargets
from services.readiness import AgentActivation, ReadinessChecker, ReadinessFacts
from services.test_data import ErasedCounts, TestDataEraser
from services.usage import UsageGuard

# Stands in for a greeting when the agent row does not exist yet: `load_agent` creates it
# with a default (non-blank) name and greeting on first use.
_DEFAULT_GREETING = "default"


class CalendarStatusOnce:
    """`CalendarConnections.status`, remembered per business for the life of one request."""

    def __init__(self, connections: CalendarConnections) -> None:
        self._connections = connections
        self._seen: dict[uuid.UUID, CalendarStatus] = {}

    async def __call__(self, business_id: uuid.UUID) -> CalendarStatus:
        if business_id not in self._seen:
            self._seen[business_id] = await self._connections.status(business_id)
        return self._seen[business_id]


class SqlReadinessFacts:
    """Gathers `ReadinessFacts` for one business. Database reads first: the calendar
    status may call Google (it commits before the call)."""

    def __init__(
        self,
        session: AsyncSession,
        calendar_status: Callable[[uuid.UUID], Awaitable[CalendarStatus]],
        calendar_configured: bool,
        targets: WebhookTargets,
    ) -> None:
        self._session = session
        self._calendar_status = calendar_status
        self._calendar_configured = calendar_configured
        self._targets = targets

    async def __call__(self, business_id: uuid.UUID) -> ReadinessFacts:
        agent = await get_agent(self._session, business_id)
        services = await repo.count_active_services(self._session, business_id)
        hours = await repo.has_hours(self._session, business_id)
        contact = await repo.has_escalation_contact(self._session, business_id)
        numbers = await repo.phone_numbers(self._session, business_id)
        tested = await repo.has_tested_chat(self._session, business_id)
        calendar = await self._calendar_status(business_id)
        statuses = [n.status for n in numbers]
        if "connected" in statuses:
            phone_status = "connected"
        elif statuses:
            phone_status = "misconfigured"
        else:
            phone_status = "not_connected"
        return ReadinessFacts(
            agent_name=agent.name if agent else DEFAULT_NAME,
            agent_greeting=agent.greeting if agent else _DEFAULT_GREETING,
            active_services=services,
            hours_set=hours,
            calendar_configured=self._calendar_configured,
            calendar_status=calendar.status,
            escalation_contact=contact,
            phone_configured=self._targets.configured,
            phone_status=phone_status,
            test_chat_done=tested,
        )


class SqlIntegrationStore:
    """`services.integration_status.IntegrationStore` over one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def calendar_updated_at(self, business_id: uuid.UUID) -> datetime | None:
        return await repo.calendar_updated_at(self._session, business_id)

    async def phone_numbers(self, business_id: uuid.UUID) -> Sequence[PhoneRecord]:
        return [
            PhoneRecord(n.phone_number, n.status, n.last_error, n.verified_at, n.updated_at)
            for n in await repo.phone_numbers(self._session, business_id)
        ]

    async def failed_sms(self, business_id: uuid.UUID, since: datetime) -> Sequence[SmsFailure]:
        return [
            SmsFailure(code, at)
            for code, at in await repo.failed_sms_alerts(self._session, business_id, since)
        ]


class SqlMetricsQueries:
    """`services.dashboard.MetricsQueries` over one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def counts(
        self, business_id: uuid.UUID, starts_at: datetime, ends_before: datetime
    ) -> MetricCounts:
        total, bookings, faqs, escalations = await dashboard_repo.dashboard_counts(
            self._session, business_id, starts_at, ends_before
        )
        return MetricCounts(total, bookings, faqs, escalations)


class SqlTestDataStore:
    """`services.test_data.TestDataStore` over the request's session."""

    __test__ = False  # not a pytest class, despite the name

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def calendar_event_ids(self, business_id: uuid.UUID) -> list[str]:
        return await test_data_repo.calendar_event_ids(self._session, business_id)

    async def erase(self, business_id: uuid.UUID, *, keep_usage_from: date) -> ErasedCounts:
        counts, event_ids = await test_data_repo.erase_business_test_data(
            self._session, business_id, keep_usage_from=keep_usage_from
        )
        return ErasedCounts(**counts, calendar_event_ids=tuple(event_ids))


def get_calendar_status_source(
    connections: CalendarConnections = Depends(get_calendar_connections),
) -> CalendarStatusOnce:
    return CalendarStatusOnce(connections)


def get_agent_activation(
    session: AsyncSession = Depends(get_session),
    store: SqlAgentStore = Depends(get_agent_store),
    status: CalendarStatusOnce = Depends(get_calendar_status_source),
    config: CalendarConfig = Depends(get_calendar_config),
    targets: WebhookTargets = Depends(get_webhook_targets),
) -> AgentActivation:
    facts = SqlReadinessFacts(session, status, config.client_configured, targets)
    return AgentActivation(store, ReadinessChecker(facts))


def get_integration_status(
    session: AsyncSession = Depends(get_session),
    status: CalendarStatusOnce = Depends(get_calendar_status_source),
    config: CalendarConfig = Depends(get_calendar_config),
    targets: WebhookTargets = Depends(get_webhook_targets),
    activation: AgentActivation = Depends(get_agent_activation),
    usage: UsageGuard = Depends(get_usage_guard),
    clock: Callable[[], datetime] = Depends(get_clock),
) -> IntegrationStatus:
    return IntegrationStatus(
        SqlIntegrationStore(session),
        status,
        config.client_configured,
        targets.configured,
        activation,
        usage,
        clock,
    )


def get_dashboard_metrics(
    session: AsyncSession = Depends(get_session),
    clock: Callable[[], datetime] = Depends(get_clock),
) -> DashboardMetrics:
    return DashboardMetrics(SqlMetricsQueries(session), clock)


def get_test_data_eraser(
    session: AsyncSession = Depends(get_session),
    connections: CalendarConnections = Depends(get_calendar_connections),
    usage: UsageGuard = Depends(get_usage_guard),
) -> TestDataEraser:
    return TestDataEraser(
        SqlTestDataStore(session),
        connections.cancel_event,
        lambda: usage.window().day,
    )
