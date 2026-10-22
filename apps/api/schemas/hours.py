"""Request/response models for `/api/v1/business/hours`.

One window per weekday, local wall-clock time in the business's timezone.
`day_of_week` is 0 = Monday ... 6 = Sunday. `PUT` always carries the whole
week (exactly one entry per day) and replaces it atomically; there is no
per-day PATCH, so a week can't end up half-written.
"""

from datetime import time

from pydantic import BaseModel, ConfigDict, Field, model_validator

DAYS_IN_WEEK = 7


class DayHours(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    day_of_week: int = Field(ge=0, le=6)
    is_closed: bool
    # "HH:MM[:SS]"; both null when closed, both set (opens < closes) when open.
    opens_at: time | None = None
    closes_at: time | None = None

    @model_validator(mode="after")
    def _window_matches_closed_flag(self) -> "DayHours":
        if self.is_closed:
            if self.opens_at is not None or self.closes_at is not None:
                raise ValueError("A closed day has no opening or closing time.")
        elif self.opens_at is None or self.closes_at is None:
            raise ValueError("An open day needs both an opening and a closing time.")
        elif self.opens_at >= self.closes_at:
            raise ValueError("Closing time must be after opening time on the same day.")
        return self


class WeekHoursUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[DayHours] = Field(min_length=DAYS_IN_WEEK, max_length=DAYS_IN_WEEK)

    @model_validator(mode="after")
    def _one_entry_per_day(self) -> "WeekHoursUpdate":
        if sorted(day.day_of_week for day in self.days) != list(range(DAYS_IN_WEEK)):
            raise ValueError("Send exactly one entry for each day of the week (0-6).")
        return self


class WeekHoursRead(BaseModel):
    # The business's IANA timezone: every time below is local to it.
    timezone: str
    # False until the owner first saves their hours (then `days` has all 7, Monday first).
    configured: bool
    days: list[DayHours]
