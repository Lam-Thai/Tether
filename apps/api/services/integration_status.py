"""The owner's integration status page (slice 16): calendar, phone, agent, recent errors, usage.

`IntegrationStatus.read` composes facts that already exist: the calendar status exactly as
`GET /calendar/status` reports it (it may probe Google, at most every
`CALENDAR_STATUS_CHECK_SECONDS`, metered), the business's phone numbers from the DATABASE
(no Twilio call), the activation checklist and today's usage. `errors` are fixed codes only,
newest first: the calendar's `last_error`, each number's `last_error`, and failed owner SMS
alerts of the last seven days grouped by Twilio's error code. No provider text ever reaches
it. No FastAPI or SQLAlchemy imports.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from core.tenant import Tenant
from services.agent import require_business
from services.calendar import RECONNECT_NEEDED, CalendarStatus
from services.readiness import ActivationState, AgentActivation
from services.usage import UsageGuard, UsageSummary

SMS_WINDOW = timedelta(days=7)
SMS_FAILED = "sms_failed"
NOT_CONNECTED = "not_connected"


@dataclass(frozen=True)
class PhoneRecord:
    phone_number: str
    status: str  # connected | misconfigured
    last_error: str | None
    verified_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True)
class SmsFailure:
    code: str | None
    occurred_at: datetime


@dataclass(frozen=True)
class CalendarIntegration:
    configured: bool
    status: str
    calendar_name: str | None
    last_error: str | None
    last_synced_at: datetime | None


@dataclass(frozen=True)
class PhoneIntegration:
    configured: bool
    status: str  # connected | misconfigured | not_connected
    phone_number: str | None
    last_error: str | None
    verified_at: datetime | None


@dataclass(frozen=True)
class IntegrationError:
    source: str  # calendar | phone | sms
    code: str
    occurred_at: datetime


@dataclass(frozen=True)
class IntegrationOverview:
    calendar: CalendarIntegration
    phone: PhoneIntegration
    activation: ActivationState
    errors: tuple[IntegrationError, ...]
    usage: UsageSummary


class IntegrationStore(Protocol):
    async def calendar_updated_at(self, business_id: uuid.UUID) -> datetime | None: ...
    async def phone_numbers(self, business_id: uuid.UUID) -> Sequence[PhoneRecord]:
        """Oldest first."""
        ...

    async def failed_sms(self, business_id: uuid.UUID, since: datetime) -> Sequence[SmsFailure]: ...


def best_phone(numbers: Sequence[PhoneRecord], configured: bool) -> PhoneIntegration:
    """The first connected number, else the first misconfigured one, else none."""
    chosen = next((n for n in numbers if n.status == "connected"), None) or next(
        (n for n in numbers if n.status == "misconfigured"), None
    )
    if chosen is None:
        return PhoneIntegration(configured, NOT_CONNECTED, None, None, None)
    return PhoneIntegration(
        configured, chosen.status, chosen.phone_number, chosen.last_error, chosen.verified_at
    )


class IntegrationStatus:
    def __init__(
        self,
        store: IntegrationStore,
        calendar_status: Callable[[uuid.UUID], Awaitable[CalendarStatus]],
        calendar_configured: bool,
        phone_configured: bool,
        activation: AgentActivation,
        usage: UsageGuard,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._calendar_status = calendar_status
        self._calendar_configured = calendar_configured
        self._phone_configured = phone_configured
        self._activation = activation
        self._usage = usage
        self._clock = clock

    async def read(self, tenant: Tenant) -> IntegrationOverview:
        business_id = require_business(tenant).id
        # Database reads first; the readiness check below may call Google (and commit).
        numbers = await self._store.phone_numbers(business_id)
        sms = await self._store.failed_sms(business_id, self._clock() - SMS_WINDOW)
        activation = await self._activation.state(tenant)
        status = await self._calendar_status(business_id)  # the same result readiness used
        calendar_updated_at = await self._store.calendar_updated_at(business_id)
        phone = best_phone(numbers, self._phone_configured)
        return IntegrationOverview(
            calendar=CalendarIntegration(
                configured=self._calendar_configured,
                status=status.status,
                calendar_name=status.calendar_name,
                last_error=status.last_error,
                last_synced_at=status.last_synced_at,
            ),
            phone=phone,
            activation=activation,
            errors=_errors(status, calendar_updated_at, numbers, sms, self._clock()),
            usage=await self._usage.summary(business_id),
        )


def _errors(
    calendar: CalendarStatus,
    calendar_updated_at: datetime | None,
    numbers: Sequence[PhoneRecord],
    sms: Sequence[SmsFailure],
    now: datetime,
) -> tuple[IntegrationError, ...]:
    found: list[IntegrationError] = []
    code = calendar.last_error or (
        RECONNECT_NEEDED if calendar.status == RECONNECT_NEEDED else None
    )
    if code is not None:
        found.append(IntegrationError("calendar", code, calendar_updated_at or now))
    for number in numbers:
        if number.last_error is not None:
            found.append(IntegrationError("phone", number.last_error, number.updated_at))
    for failure in sms:
        found.append(IntegrationError("sms", failure.code or SMS_FAILED, failure.occurred_at))
    return tuple(sorted(found, key=lambda e: e.occurred_at, reverse=True))
