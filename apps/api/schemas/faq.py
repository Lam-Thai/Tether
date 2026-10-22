"""Request/response models for `/api/v1/faqs` (PRD §8.4).

Limits here are the API contract; `apps/web/lib/validation/faq.ts` mirrors
them. A blank category means "no category" and is stored as null.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints, model_validator

MAX_QUESTION_CHARS = 300
MAX_ANSWER_CHARS = 1000
MAX_CATEGORY_CHARS = 60

QuestionStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)
]
AnswerStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_ANSWER_CHARS)
]
CategoryStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=MAX_CATEGORY_CHARS),
    AfterValidator(lambda value: value or None),
]


class FaqRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    question: str
    answer: str
    category: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class FaqList(BaseModel):
    faqs: list[FaqRead]
    # The per-business cap (`FAQ_MAX_PER_BUSINESS`), so the UI can say when it's reached.
    max_faqs: int


class FaqCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: QuestionStr
    answer: AnswerStr
    category: CategoryStr | None = None
    active: bool = True


class FaqUpdate(BaseModel):
    """Every field optional; only the ones sent are changed. `category: null` (or
    blank) clears the category; an explicit null for any other field is rejected."""

    model_config = ConfigDict(extra="forbid")

    question: QuestionStr | None = None
    answer: AnswerStr | None = None
    category: CategoryStr | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> "FaqUpdate":
        for field_name in self.model_fields_set - {"category"}:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null.")
        return self
