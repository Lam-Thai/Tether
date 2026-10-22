"""Request/response models for `/api/v1/services` (PRD §8.5, US-004).

Limits here are the API contract; `apps/web/lib/validation/service.ts`
mirrors them. Prices are decimals in the business's currency and go out as
decimal strings (never floats). A blank description means none.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    model_validator,
)

MAX_NAME_CHARS = 80
MAX_DESCRIPTION_CHARS = 500
MAX_DURATION_MINUTES = 1440
MAX_PRICE = Decimal("100000000")  # fits Numeric(10, 2)

NameStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_NAME_CHARS)
]
DescriptionStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=MAX_DESCRIPTION_CHARS),
    AfterValidator(lambda value: value or None),
]
DurationInt = Annotated[int, Field(ge=1, le=MAX_DURATION_MINUTES)]
PriceDecimal = Annotated[
    Decimal, Field(ge=0, lt=MAX_PRICE, max_digits=10, decimal_places=2, allow_inf_nan=False)
]
PriceOut = Annotated[Decimal, PlainSerializer(lambda value: f"{value:.2f}", return_type=str)]


class ServiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    duration_minutes: int
    # Decimal string ("45.00") in the business's currency; null = no listed price.
    price: PriceOut | None
    active: bool
    created_at: datetime
    updated_at: datetime


class ServiceList(BaseModel):
    services: list[ServiceRead]
    # ISO 4217 code every price above is in (the business's `currency`).
    currency: str
    # The per-business cap (`SERVICE_MAX_PER_BUSINESS`).
    max_services: int


class ServiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: NameStr
    description: DescriptionStr | None = None
    duration_minutes: DurationInt
    price: PriceDecimal | None = None
    active: bool = True


class ServiceUpdate(BaseModel):
    """Every field optional; only the ones sent are changed. `description: null` and
    `price: null` clear them; an explicit null for any other field is rejected."""

    model_config = ConfigDict(extra="forbid")

    name: NameStr | None = None
    description: DescriptionStr | None = None
    duration_minutes: DurationInt | None = None
    price: PriceDecimal | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> "ServiceUpdate":
        for field_name in self.model_fields_set - {"description", "price"}:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null.")
        return self
