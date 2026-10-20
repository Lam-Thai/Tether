from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SubscriptionRead(BaseModel):
    model_config = {"from_attributes": True}

    entitled: bool
    status: str | None
    trial_ends_at: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    trial_available: bool


class PlanRead(BaseModel):
    model_config = {"from_attributes": True}

    amount: int
    currency: str
    interval: str
    trial_days: int
    trial_available: bool


class CheckoutCreate(BaseModel):
    choice: Literal["trial", "paid"]


class RedirectRead(BaseModel):
    url: str


# Stripe Checkout Session ids; anything else is rejected before reaching Stripe.
CHECKOUT_SESSION_ID_PATTERN = r"^cs_(test|live)_[A-Za-z0-9]{1,200}$"


class WebhookAck(BaseModel):
    received: Literal[True] = True
