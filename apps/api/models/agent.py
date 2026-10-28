"""`agents`: the owner's receptionist persona. One row per business.

Created lazily with defaults on first read (`repositories/agents.py`), so
the unique `business_id` is also what makes get-or-create race-safe.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        CheckConstraint("tone IN ('friendly', 'professional', 'concise')", name="ck_agents_tone"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    tone: Mapped[str] = mapped_column(String, nullable=False)
    greeting: Mapped[str] = mapped_column(Text, nullable=False)
    custom_instructions: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    # TTS voice name; NULL = the settings default voice, 'standard' = Twilio <Say>.
    # No CHECK: the allowed list comes from settings.
    voice: Mapped[str | None] = mapped_column(String, nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
