"""`faqs`: the owner's question-and-answer entries (PRD §8.4, §10.1).

The agent may state a business fact only if it came back from `search_faq`
this turn, and `search_faq` reads only `active` rows (`services/knowledge.py`).
Disabling an FAQ (`active = false`) removes it from every model request at
once; nothing is cached.

`search_vector` is a stored generated column (question weighted above
answer, then category) with a GIN index, so ranking is plain Postgres
full-text search and never has to be kept in sync by application code.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

# Immutable (explicit regconfig), as a generated column requires.
FAQ_SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('english'::regconfig, coalesce(question, '')), 'A') || "
    "setweight(to_tsvector('english'::regconfig, coalesce(answer, '')), 'B') || "
    "setweight(to_tsvector('english'::regconfig, coalesce(category, '')), 'C')"
)


class Faq(Base):
    __tablename__ = "faqs"
    __table_args__ = (
        Index("ix_faqs_business_id_active", "business_id", "active"),
        Index("ix_faqs_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed(FAQ_SEARCH_VECTOR_SQL, persisted=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
