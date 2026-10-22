"""`interaction_operations`: the operation log. One row per tool call the model
requested during a conversation, whatever happened to it.

`succeeded`: arguments validated and the executor returned a verified result.
`rejected`: arguments failed validation; nothing was executed.
`failed`: the executor raised; the model was told the tool is unavailable.

`arguments` holds the *validated* arguments, sanitized and truncated
(`services/tools.py`); a rejected call stores only the argument names it
was sent, never their values. A validated argument can be customer text
(`search_faq`'s query, at most 200 chars): the same sensitivity as
`interaction_messages`, scoped and cascaded with the business the same way.
`result` is a short machine summary (ids, counts, a reason code), never free
text from the model or the customer.
The inbox's "verified backend actions" view reads from this table.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

OPERATION_STATUSES = ("succeeded", "rejected", "failed")
_STATUS_LIST = ", ".join(f"'{status}'" for status in OPERATION_STATUSES)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InteractionOperation(Base):
    __tablename__ = "interaction_operations"
    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_LIST})", name="ck_interaction_operations_status"),
        CheckConstraint("duration_ms >= 0", name="ck_interaction_operations_duration"),
        Index(
            "ix_interaction_operations_business_interaction_created",
            "business_id",
            "interaction_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    interaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interactions.id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=_utcnow, nullable=False
    )
