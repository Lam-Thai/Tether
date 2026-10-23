"""`calendar_connections`: one Google Calendar link per business.

The refresh token is stored ONLY as AES-GCM ciphertext (`encrypted_refresh_token`,
sealed by `services/token_vault.TokenVault`, bound to the business) plus the id of
the key that sealed it. No access token is ever persisted: they live for an hour
and are refreshed on demand. `connection_status` is the last known state; the
absence of a row means "not connected".
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class CalendarConnection(Base):
    __tablename__ = "calendar_connections"
    __table_args__ = (
        CheckConstraint("provider IN ('google')", name="ck_calendar_connections_provider"),
        CheckConstraint(
            "connection_status IN ('needs_selection', 'connected', 'reconnect_needed')",
            name="ck_calendar_connections_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # UNIQUE: at most one connection per business (and it backs the lookup by business).
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(
        String(16), nullable=False, default="google", server_default="google"
    )
    encrypted_refresh_token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    granted_scopes: Mapped[str] = mapped_column(Text, nullable=False)
    calendar_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    calendar_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connection_status: Mapped[str] = mapped_column(String(32), nullable=False)
    # A fixed reason code (auth_revoked, token_unreadable, unavailable, calendar_missing).
    last_error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
