from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_serializer


class UsageWindowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timezone: Literal["UTC"]
    starts_at: datetime
    resets_at: datetime


class MeterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    used: int
    limit: int
    # Only a flag: the platform-wide numbers are other tenants' business.
    global_limit_reached: bool


class ConcurrencyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    active: int
    limit: int
    idle_minutes: int
    global_limit_reached: bool


class UsageRead(BaseModel):
    """Today's counters for the caller's business vs. its limits (all from DB records)."""

    model_config = ConfigDict(from_attributes=True)

    window: UsageWindowRead
    meters: list[MeterRead]
    gemini_tokens: int
    failed_requests: int
    estimated_cost_usd: Decimal
    concurrency: ConcurrencyRead
    max_call_duration_seconds: int

    @field_serializer("estimated_cost_usd")
    def _cost_as_string(self, value: Decimal) -> str:
        # A decimal string, never a float: no binary rounding on money.
        return f"{value.quantize(Decimal('0.000001')):f}"
