"""Request/response models for `/api/v1/calendar`.

Responses carry the connection's state and the chosen calendar's display name, never a
token, an authorization code, a scope string or a key id.
"""

from datetime import datetime
from typing import Literal

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
