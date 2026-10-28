"""`media_objects` and `media_tokens`: short-lived call audio, kept in Postgres.

Serverless has no durable filesystem, so the audio Twilio plays lives in a `bytea` column
(see docs/adr/0005-short-lived-call-audio-in-postgres.md). Two tables so every URL token
expires within minutes even when the audio itself is cached for longer: an object may carry
a `cache_key` (unique per business while set) and outlive many tokens. Only the SHA-256 of a
token is stored, so reading the database never yields a working URL.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

MAX_MEDIA_BYTES = 5 * 1024 * 1024


class MediaObject(Base):
    __tablename__ = "media_objects"
    __table_args__ = (
        CheckConstraint("content_type <> ''", name="ck_media_objects_content_type"),
        CheckConstraint(f"octet_length(data) <= {MAX_MEDIA_BYTES}", name="ck_media_objects_size"),
        Index(
            "uq_media_objects_cache_key",
            "business_id",
            "cache_key",
            unique=True,
            postgresql_where=text("cache_key IS NOT NULL"),
        ),
        Index("ix_media_objects_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    cache_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MediaToken(Base):
    __tablename__ = "media_tokens"
    __table_args__ = (
        CheckConstraint("octet_length(token_hash) = 32", name="ck_media_tokens_hash_length"),
        Index("ix_media_tokens_expires_at", "expires_at"),
    )

    # sha256(token); the token itself is never stored.
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_objects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
