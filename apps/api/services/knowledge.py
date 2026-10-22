"""`KnowledgeBase`: which of the owner's FAQs are relevant to a question.

One method, `relevant(tenant, query, limit)`, hides the ranking:

- Only active FAQs, always. A disabled FAQ can never reach the model.
- A business with no more active FAQs than `limit` gets all of them: with
  so few, ranking would only risk hiding the right one.
- Otherwise Postgres full-text search over question, answer and category
  (any query term may match, best rank first). No match means an empty
  list, never the whole table: unrelated FAQs are not injected (PRD §8.4).

Reads the store on every call, so an edit applies to the very next turn.
No FastAPI or SQLAlchemy imports; the store is bound to a session upstream.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from core.tenant import Tenant
from models.faq import Faq
from services.agent import require_business

DEFAULT_LIMIT = 5
MAX_LIMIT = 10
MAX_QUERY_CHARS = 200


@dataclass(frozen=True)
class FaqSnippet:
    id: uuid.UUID
    question: str
    answer: str
    category: str | None


class FaqSearchStore(Protocol):
    async def count_active_faqs(self, business_id: uuid.UUID) -> int: ...
    async def list_active_faqs(self, business_id: uuid.UUID, limit: int) -> Sequence[Faq]:
        """Active FAQs in a stable order (category, then oldest first)."""
        ...

    async def search_active_faqs(
        self, business_id: uuid.UUID, query: str, limit: int
    ) -> Sequence[Faq]:
        """Active FAQs matching any term of `query`, best full-text rank first."""
        ...


def _snippet(faq: Faq) -> FaqSnippet:
    return FaqSnippet(id=faq.id, question=faq.question, answer=faq.answer, category=faq.category)


class KnowledgeBase:
    def __init__(self, store: FaqSearchStore) -> None:
        self._store = store

    async def relevant(
        self, tenant: Tenant, query: str, limit: int = DEFAULT_LIMIT
    ) -> list[FaqSnippet]:
        business = require_business(tenant)
        limit = max(1, min(limit, MAX_LIMIT))
        if await self._store.count_active_faqs(business.id) <= limit:
            return [_snippet(f) for f in await self._store.list_active_faqs(business.id, limit)]
        terms = " ".join(query.split())[:MAX_QUERY_CHARS]
        if not terms:
            return []
        found = await self._store.search_active_faqs(business.id, terms, limit)
        return [_snippet(f) for f in found]
