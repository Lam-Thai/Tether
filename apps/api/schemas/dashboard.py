"""Request/response models for the dashboard, integration status, activation checklist and
"delete test data" routes (slice 16). Fixed codes and counts only, never provider text."""

from datetime import date, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.usage import UsageRead

if TYPE_CHECKING:
    from services.readiness import ActivationState


class DashboardSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    # The inclusive business-local range the counts cover (`from` / `to` on the wire).
    from_: date = Field(serialization_alias="from")
    to: date
    timezone: str
    total_interactions: int
    successful_bookings: int
    faqs_answered: int
    open_escalations: int


class ReadinessItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    ok: bool
    required: bool
    message: str
    fix_url: str


class ReadinessRead(BaseModel):
    active: bool
    ready: bool
    items: list[ReadinessItemRead]

    @classmethod
    def from_activation(cls, state: "ActivationState") -> "ReadinessRead":
        return cls(
            active=state.active,
            ready=state.readiness.ready,
            items=[ReadinessItemRead.model_validate(item) for item in state.readiness.items],
        )


class AgentStatusRead(BaseModel):
    active: bool
    ready: bool


class CalendarIntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    configured: bool
    status: Literal["not_connected", "needs_selection", "connected", "reconnect_needed"]
    calendar_name: str | None
    last_error: str | None
    last_synced_at: datetime | None


class PhoneIntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    configured: bool
    status: Literal["connected", "misconfigured", "not_connected"]
    phone_number: str | None
    last_error: str | None
    verified_at: datetime | None


class IntegrationErrorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: Literal["calendar", "phone", "sms"]
    code: str
    occurred_at: datetime


class IntegrationStatusRead(BaseModel):
    calendar: CalendarIntegrationRead
    phone: PhoneIntegrationRead
    agent: AgentStatusRead
    readiness: ReadinessRead
    errors: list[IntegrationErrorRead]
    usage: UsageRead


class TestDataDelete(BaseModel):
    __test__ = False  # not a pytest class, despite the name

    # Checked by the service (422 `confirmation_mismatch`), so a wrong or empty phrase is
    # that error and not a generic validation error. Bounded so a huge body is refused.
    confirmation: str = Field(max_length=200)


class ErasureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    interactions: int
    messages: int
    bookings: int
    escalations: int
    usage_events: int
    calendar_events_cancelled: int
    calendar_events_not_cancelled: int
