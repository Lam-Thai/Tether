"""`AgentTurn`: the structured reply the model must produce on every turn.

The backend validates the model's raw output against this before anything
is persisted or shown (PRD §8.6). A reply that doesn't parse, or that asks
for a tool the `ToolRegistry` doesn't have, is invalid: the engine retries
once, then falls back. Nothing in here is ever executed unvalidated: tool
arguments are validated again, per tool, by `services/tools.ToolExecutor`.

`sources` lists what a reply relies on: FAQ and service ids a tool returned
this turn, `business_hours` or `business_profile`. They stay plain strings
here on purpose: a made-up id must reach the engine's grounding guard
(`services/grounding.py`, which replaces the reply with the "I don't have
that information" fallback), not make the whole reply schema-invalid and
burn the one retry.
"""

from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

Intent = Literal[
    "FAQ",
    "BOOK_APPOINTMENT",
    "BUSINESS_HOURS",
    "LOCATION",
    "PRICE_INQUIRY",
    "RESCHEDULE",
    "CANCEL",
    "HUMAN_REQUEST",
    "COMPLAINT",
    "URGENT",
    "UNKNOWN",
]

MAX_RESPONSE_CHARS = 1000
MAX_ENTITY_CHARS = 80
MAX_SOURCES = 10
MAX_SOURCE_CHARS = 64

# Stripped before the length checks, so a whitespace-only reply is invalid.
ResponseText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_RESPONSE_CHARS)
]
# Entities are model-extracted from customer speech and re-enter the prompt on later
# turns, so each value is short and stripped. A blank value means unknown and becomes
# None, so merging state (exclude_none) never overwrites an earlier extraction with "".
EntityText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=MAX_ENTITY_CHARS),
    AfterValidator(lambda value: value or None),
]
# Room for a UUID and then some; whether it's a real, retrieved source is the guard's call.
SourceId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_SOURCE_CHARS)
]


class Entities(BaseModel):
    """What the model extracted so far. Every field is optional; null means unknown."""

    model_config = ConfigDict(extra="ignore")

    service_name: EntityText | None = None
    requested_date: EntityText | None = None
    requested_time: EntityText | None = None
    customer_name: EntityText | None = None
    customer_phone: EntityText | None = None


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentTurn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Intent
    response_text: ResponseText
    entities: Entities = Field(default_factory=Entities)
    requires_human: bool = False
    # Backend tools to run before answering. A reply that requests tools is never
    # shown: the engine runs them and asks the model again with the results.
    tool_calls: list[ToolCall] = Field(default_factory=list)
    # What the reply relies on: FAQ / service ids retrieved this turn, "business_hours",
    # "business_profile" (checked by `services/grounding.py`).
    sources: list[SourceId] = Field(default_factory=list, max_length=MAX_SOURCES)
