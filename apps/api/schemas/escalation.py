"""Request/response models for escalations and `/api/v1/business/escalation-settings`."""

import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Reason = Literal[
    "urgent",
    "human_requested",
    "complaint",
    "change_request",
    "booking_unresolved",
    "integration_failing",
    "clarification_exhausted",
]
Urgency = Literal["normal", "high"]
EscalationStatus = Literal["open", "resolved"]
SmsStatus = Literal[
    "pending",
    "disabled",
    "logged",
    "skipped",
    "accepted",
    "queued",
    "sending",
    "sent",
    "delivered",
    "undelivered",
    "failed",
]

_E164 = re.compile(r"\+[1-9][0-9]{6,14}")
Keyword = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class EscalationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    interaction_id: UUID
    reason: Reason
    urgency: Urgency
    status: EscalationStatus
    sms_status: SmsStatus
    # A fixed code (`limit_reached`, ...) or Twilio's numeric error code, never provider text.
    sms_error: str | None
    created_at: datetime
    resolved_at: datetime | None


class EscalationListRead(BaseModel):
    items: list[EscalationRead]


class EscalationResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["resolved"]


class EscalationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_phone: str | None
    sms_enabled: bool
    clarification_threshold: int = Field(ge=1, le=5)
    urgent_keywords: list[Keyword] = Field(max_length=20)

    @field_validator("contact_phone")
    @classmethod
    def _phone_is_e164(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not _E164.fullmatch(value):
            raise ValueError("contact_phone must be in E.164 format, like +15551234567")
        return value


class EscalationSettingsRead(EscalationSettingsUpdate):
    # False: nothing saved yet, the values are the defaults.
    customized: bool
    # False: Twilio isn't configured, so alerts are only recorded and logged (demo mode).
    sms_configured: bool
