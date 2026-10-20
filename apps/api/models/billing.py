"""Billing state: one `subscriptions` row per business, plus the webhook ledger.

The row exists from a business's first checkout attempt (it holds the
Stripe customer id) and mirrors Stripe's subscription once one exists.
`status` is NULL until then. Stripe is the source of truth; this is the
copy the entitlement check reads.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    price_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # At most one open Checkout Session per business; a new checkout expires it.
    pending_checkout_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class StripeEvent(Base):
    """Idempotency ledger: a webhook event id is processed at most once."""

    __tablename__ = "stripe_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
