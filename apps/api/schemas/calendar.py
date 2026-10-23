"""Request/response models for `/api/v1/calendar`.

Responses carry the connection's state and the chosen calendar's display name, never a
token, an authorization code, a scope string or a key id.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConnectUrlRead(BaseModel):
    url: str


class CallbackCreate(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=1, max_length=4096)


class CalendarSelect(BaseModel):
    calendar_id: str = Field(min_length=1, max_length=1024)


class CalendarStatusRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: Literal["not_connected", "needs_selection", "connected", "reconnect_needed"]
    calendar_id: str | None
    calendar_name: str | None
    last_error: str | None
    last_synced_at: datetime | None


class CalendarOptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    primary: bool
    time_zone: str | None


class CalendarListRead(BaseModel):
    calendars: list[CalendarOptionRead]


class DisconnectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    revoked: bool


class SlotRead(BaseModel):
    start: datetime  # aware, in the business timezone
    end: datetime
    label: str  # local "HH:MM", what the agent offers


class AvailabilityServiceRead(BaseModel):
    id: UUID
    name: str
    duration_minutes: int


class AvailabilityRead(BaseModel):
    """`GET /calendar/availability`: what the agent would see for one service and date."""

    status: Literal["available", "no_availability", "calendar_unavailable"]
    date: date
    timezone: str
    service: AvailabilityServiceRead
    # Every free start that day ("available" only; empty otherwise).
    slots: list[SlotRead] = Field(default_factory=list)
    # The <= 3 the agent would offer with no preference.
    offered: list[SlotRead] = Field(default_factory=list)
    reason: (
        Literal[
            "past_date",
            "beyond_horizon",
            "closed",
            "hours_not_set",
            "service_too_long",
            "no_time_left",
            "too_soon",
            "fully_booked",
            "not_connected",
            "reconnect_needed",
            "unavailable",
        ]
        | None
    ) = None
