from datetime import datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import available_timezones

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_validator,
    model_validator,
)

_IANA_ZONES = available_timezones()

MAX_NAME_CHARS = 120
MAX_INDUSTRY_CHARS = 80
MAX_ADDRESS_CHARS = 300
MAX_PHONE_CHARS = 40
MAX_EMAIL_CHARS = 254

# ISO 4217 codes a business may price in, with their minor-unit digits (for
# display; storage is always Numeric(10, 2)). `apps/web/lib/validation/business.ts`
# mirrors this list.
CURRENCIES: dict[str, int] = {
    "USD": 2,
    "CAD": 2,
    "EUR": 2,
    "GBP": 2,
    "AUD": 2,
    "NZD": 2,
    "CHF": 2,
    "SEK": 2,
    "NOK": 2,
    "DKK": 2,
    "SGD": 2,
    "HKD": 2,
    "INR": 2,
    "MXN": 2,
    "BRL": 2,
    "ZAR": 2,
    "PHP": 2,
    "THB": 2,
    "CNY": 2,
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
}


def _validate_timezone(value: str) -> str:
    if value not in _IANA_ZONES:
        raise ValueError(f"'{value}' is not a known IANA timezone name.")
    return value


def _validate_currency(value: str) -> str:
    if value not in CURRENCIES:
        raise ValueError(f"'{value}' is not a supported currency code.")
    return value


def _validate_email(value: str | None) -> str | None:
    # Shape check only; delivery is never attempted from here.
    if value is not None and (value.count("@") != 1 or "." not in value.split("@")[1]):
        raise ValueError("Enter a valid email address.")
    return value


NameStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_NAME_CHARS)
]
IndustryStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_INDUSTRY_CHARS)
]
AddressStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_ADDRESS_CHARS)
]
# Optional contact fields: blank means "none" and is stored as null.
PhoneStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=MAX_PHONE_CHARS),
    AfterValidator(lambda value: value or None),
]
EmailStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=MAX_EMAIL_CHARS),
    AfterValidator(lambda value: value or None),
    AfterValidator(_validate_email),
]
TimezoneStr = Annotated[str, AfterValidator(_validate_timezone)]
CurrencyStr = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True), AfterValidator(_validate_currency)
]


class BusinessCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: NameStr
    industry: IndustryStr
    address: AddressStr
    timezone: TimezoneStr
    phone: PhoneStr | None = None
    contact_email: EmailStr | None = None
    currency: CurrencyStr = "USD"


class BusinessUpdate(BaseModel):
    """Every field optional; only the ones sent are changed. `phone: null` and
    `contact_email: null` (or blank) clear them; an explicit null for any other
    field is rejected."""

    model_config = ConfigDict(extra="forbid")

    name: NameStr | None = None
    industry: IndustryStr | None = None
    address: AddressStr | None = None
    timezone: TimezoneStr | None = None
    phone: PhoneStr | None = None
    contact_email: EmailStr | None = None
    currency: CurrencyStr | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone_is_valid(cls, value: str | None) -> str | None:
        return value if value is None else _validate_timezone(value)

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> "BusinessUpdate":
        for field_name in self.model_fields_set - {"phone", "contact_email"}:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null.")
        return self


class BusinessRead(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    name: str
    industry: str
    address: str
    timezone: str
    phone: str | None
    contact_email: str | None
    currency: str
    created_at: datetime
    updated_at: datetime
