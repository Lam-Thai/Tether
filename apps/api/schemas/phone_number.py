"""Request/response models for `/api/v1/phone-numbers`."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

PhoneNumberStatus = Literal["connected", "misconfigured"]
PhoneNumberError = Literal[
    "voice_url_mismatch", "status_callback_mismatch", "number_missing", "unavailable"
]


class PhoneNumberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    phone_number: str
    provider_number_sid: str
    status: PhoneNumberStatus
    last_error: PhoneNumberError | None
    verified_at: datetime | None
    created_at: datetime


class AvailableNumberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider_number_sid: str
    phone_number: str
    friendly_name: str | None


class PhoneNumbersRead(BaseModel):
    configured: bool
    trial: bool | None
    numbers: list[PhoneNumberRead]
    available: list[AvailableNumberRead]
    available_error: Literal["unavailable", "not_configured"] | None


class PhoneNumberAssociate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Twilio's IncomingPhoneNumber sid; the shape check keeps anything else off the URL.
    provider_number_sid: str = Field(pattern=r"^PN[0-9a-f]{32}$")


class PhoneNumberDisconnectRead(BaseModel):
    webhooks_cleared: bool
