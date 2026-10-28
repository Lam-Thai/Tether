from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Tone = Literal["friendly", "professional", "concise"]

NameStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
GreetingStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
InstructionsStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
MessageStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
# A voice id from `GET /agent/voices`; which ids exist is settings-driven, checked by the service.
VoiceStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    tone: Tone
    greeting: str
    custom_instructions: str
    active: bool
    # The effective voice: a Gemini voice name, or `standard` (Twilio's <Say>).
    voice: str
    updated_at: datetime


class AgentUpdate(BaseModel):
    """Every field optional; only the ones sent are changed. An explicit null is rejected."""

    name: NameStr | None = None
    tone: Tone | None = None
    greeting: GreetingStr | None = None
    custom_instructions: InstructionsStr | None = None
    voice: VoiceStr | None = None

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> "AgentUpdate":
        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null.")
        return self


class VoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    description: str


class VoicesRead(BaseModel):
    voices: list[VoiceRead]
    default_voice: str
    # False: no Gemini key, so every call uses the standard voice whatever is picked.
    tts_configured: bool


class VoicePreviewCreate(BaseModel):
    voice: VoiceStr


class TestTurnCreate(BaseModel):
    __test__ = False  # not a pytest class, despite the name

    interaction_id: UUID | None = None
    message: MessageStr


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence_number: int
    speaker: Literal["agent", "customer"]
    text: str
    created_at: datetime
    fallback: bool


class TurnRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    interaction_id: UUID
    reply_text: str
    intent: str
    status: Literal["in_progress", "completed", "failed", "abandoned"]
    ended: bool
    fallback: bool
    # The reply is the quota fallback: a daily usage limit was reached.
    usage_limited: bool
    messages: list[MessageRead] = Field(default_factory=list)
    # This turn needed a person: an escalation is recorded for the owner's inbox.
    escalated: bool = False


class TranscriptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    interaction_id: UUID
    status: Literal["in_progress", "completed", "failed", "abandoned"]
    ended: bool
    started_at: datetime
    ended_at: datetime | None
    messages: list[MessageRead] = Field(default_factory=list)
