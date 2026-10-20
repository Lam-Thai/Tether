from datetime import datetime
from uuid import UUID
from zoneinfo import available_timezones

from pydantic import BaseModel, field_validator

_IANA_ZONES = available_timezones()


def _validate_timezone(value: str) -> str:
    if value not in _IANA_ZONES:
        raise ValueError(f"'{value}' is not a known IANA timezone name.")
    return value


class BusinessCreate(BaseModel):
    name: str
    industry: str
    address: str
    timezone: str
    phone: str | None = None
    contact_email: str | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone_is_valid(cls, value: str) -> str:
        return _validate_timezone(value)


class BusinessUpdate(BaseModel):
    name: str | None = None
    industry: str | None = None
    address: str | None = None
    timezone: str | None = None
    phone: str | None = None
    contact_email: str | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone_is_valid(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_timezone(value)


class BusinessRead(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    name: str
    industry: str
    address: str
    timezone: str
    phone: str | None
    contact_email: str | None
    created_at: datetime
    updated_at: datetime
