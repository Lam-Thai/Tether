from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

InteractionStatus = Literal["in_progress", "completed", "failed", "abandoned"]
InteractionOutcome = Literal["booked", "answered", "escalated", "failed", "abandoned"]
SummarySource = Literal["ai", "template"]
OperationStatus = Literal["succeeded", "rejected", "failed"]


class InteractionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel: Literal["test_chat", "voice"]
    started_at: datetime
    ended_at: datetime | None
    caller: str | None
    intent: str | None
    summary: str | None
    summary_source: SummarySource | None
    status: InteractionStatus
    outcome: InteractionOutcome | None
    requires_human: bool


class InteractionPageRead(BaseModel):
    items: list[InteractionRead]
    next_cursor: str | None
    # The business timezone: the web shows every time in it.
    timezone: str


class OperationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tool_name: str
    status: OperationStatus
    label: str | None
    created_at: datetime
    duration_ms: int


class InteractionDetailRead(InteractionRead):
    timezone: str
    message_count: int
    operations: list[OperationRead] = Field(default_factory=list)


class InboxMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence_number: int
    speaker: Literal["agent", "customer"]
    text: str
    created_at: datetime
    fallback: bool


class MessagePageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[InboxMessageRead]
    next_after: int | None
