"""In-memory `FaqSearchStore` and operation log, same contracts as the SQL ones, for unit tests."""

import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from models.faq import Faq

_WORD = re.compile(r"[a-z0-9]+")


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2}


class MemoryFaqStore:
    """Scoped by business id like `SqlFaqStore`; only active rows are ever returned.
    Search ranks by shared words (any term may match), a stand-in for full-text search."""

    def __init__(self) -> None:
        self.faqs: list[Faq] = []
        # Store calls made, so a test can prove a rejected call never reached the store.
        self.reads = 0
        self.fail = False

    def add(
        self,
        business_id: uuid.UUID,
        question: str,
        answer: str,
        category: str | None = None,
        *,
        active: bool = True,
    ) -> Faq:
        faq = Faq(
            id=uuid.uuid4(),
            business_id=business_id,
            question=question,
            answer=answer,
            category=category,
            active=active,
            created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=len(self.faqs)),
        )
        self.faqs.append(faq)
        return faq

    def _active(self, business_id: uuid.UUID) -> list[Faq]:
        self.reads += 1
        if self.fail:
            raise RuntimeError("faq store down")
        return [f for f in self.faqs if f.business_id == business_id and f.active]

    async def count_active_faqs(self, business_id: uuid.UUID) -> int:
        return len(self._active(business_id))

    async def list_active_faqs(self, business_id: uuid.UUID, limit: int) -> list[Faq]:
        rows = sorted(self._active(business_id), key=lambda f: (f.category or "", f.created_at))
        return rows[:limit]

    async def search_active_faqs(self, business_id: uuid.UUID, query: str, limit: int) -> list[Faq]:
        wanted = _terms(query)
        scored = [
            (len(wanted & _terms(f"{f.question} {f.answer} {f.category or ''}")), f)
            for f in self._active(business_id)
        ]
        hits = [(score, f) for score, f in scored if score]
        hits.sort(key=lambda pair: -pair[0])
        return [f for _, f in hits[:limit]]


class MemoryOperationLog:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record_operation(
        self,
        *,
        business_id: uuid.UUID,
        interaction_id: uuid.UUID,
        tool_name: str,
        arguments: Mapping[str, Any],
        status: str,
        result: Mapping[str, Any],
        duration_ms: int,
    ) -> None:
        self.rows.append(
            {
                "business_id": business_id,
                "interaction_id": interaction_id,
                "tool_name": tool_name,
                "arguments": dict(arguments),
                "status": status,
                "result": dict(result),
                "duration_ms": duration_ms,
            }
        )
