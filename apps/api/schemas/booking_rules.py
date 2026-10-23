"""Request/response models for `/api/v1/business/booking-rules`."""

from pydantic import BaseModel, ConfigDict, Field


class BookingRulesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_interval_minutes: int = Field(ge=5, le=240, multiple_of=5)
    min_notice_minutes: int = Field(ge=0, le=20160)
    max_days_ahead: int = Field(ge=1, le=365)
    buffer_minutes: int = Field(ge=0, le=240)


class BookingRulesRead(BookingRulesUpdate):
    # False: nothing saved yet, the values are the platform defaults.
    customized: bool
