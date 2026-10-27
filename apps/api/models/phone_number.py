"""`phone_numbers`: a Twilio number on the platform account, associated with a business.

UNIQUE `phone_number` and `provider_number_sid`: a number belongs to at most ONE business,
so an inbound call resolves to exactly one tenant. Tether never buys numbers; a row only
records that the owner associated a number already on the account and that its Voice and
Status webhooks point at this API. Disconnecting deletes the row. `status` is the last
verified webhook configuration; `last_error` is a fixed code, never provider text.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

STATUSES = ("connected", "misconfigured")
ERRORS = ("voice_url_mismatch", "status_callback_mismatch", "number_missing", "unavailable")
_STATUS_LIST = ", ".join(f"'{value}'" for value in STATUSES)
_ERROR_LIST = ", ".join(f"'{value}'" for value in ERRORS)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PhoneNumber(Base):
    __tablename__ = "phone_numbers"
    __table_args__ = (
        CheckConstraint("provider IN ('twilio')", name="ck_phone_numbers_provider"),
        CheckConstraint(f"status IN ({_STATUS_LIST})", name="ck_phone_numbers_status"),
        CheckConstraint(
            f"last_error IS NULL OR last_error IN ({_ERROR_LIST})",
            name="ck_phone_numbers_last_error",
        ),
        CheckConstraint(
            "phone_number ~ '^\\+[1-9][0-9]{6,14}$'", name="ck_phone_numbers_phone_number"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(
        String(16), nullable=False, default="twilio", server_default="twilio"
    )
    # E.164; what Twilio sends as `To` on an inbound call.
    phone_number: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    # Twilio's IncomingPhoneNumber sid (PN + 32 hex).
    provider_number_sid: Mapped[str] = mapped_column(String(34), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=_utcnow,
        onupdate=func.now(),
        nullable=False,
    )
