"""Repository for `faqs`. Every query is scoped by `business_id` from `TenantContext`.

Search is Postgres full-text search over the stored `search_vector`, with OR
semantics: `plainto_tsquery` ANDs the terms, so its `&` is swapped for `|`.
The query text is only ever a bound parameter.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.faq import Faq
from repositories.base import business_scoped_select

# Constant SQL; the user text is the bound :q.
_OR_QUERY = "replace(plainto_tsquery('english', :q)::text, '&', '|')::tsquery"


async def list_faqs(session: AsyncSession, business_id: uuid.UUID) -> Sequence[Faq]:
    result = await session.execute(
        business_scoped_select(Faq, business_id).order_by(Faq.created_at, Faq.id)
    )
    return result.scalars().all()


async def get_faq(session: AsyncSession, business_id: uuid.UUID, faq_id: uuid.UUID) -> Faq | None:
    result = await session.execute(business_scoped_select(Faq, business_id).where(Faq.id == faq_id))
    return result.scalar_one_or_none()


async def lock_and_count_faqs(session: AsyncSession, business_id: uuid.UUID) -> int:
    """Serializes FAQ creation per business (transaction-level advisory lock), then
    counts every FAQ, so the cap holds under concurrent creates."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('faqs:' || CAST(:b AS text), 0))"),
        {"b": str(business_id)},
    )
    return await count_faqs(session, business_id)


async def count_faqs(session: AsyncSession, business_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count()).select_from(Faq).where(Faq.business_id == business_id)
    )
    return result.scalar_one()


async def create_faq(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    question: str,
    answer: str,
    category: str | None,
    active: bool,
) -> Faq:
    faq = Faq(
        business_id=business_id, question=question, answer=answer, category=category, active=active
    )
    session.add(faq)
    await session.flush()
    await session.refresh(faq)
    return faq


async def save_faq(session: AsyncSession, faq: Faq) -> Faq:
    """Flushes edits, then reloads server-computed columns (`updated_at`, `search_vector`)."""
    await session.flush()
    await session.refresh(faq)
    return faq


async def delete_faq(session: AsyncSession, faq: Faq) -> None:
    await session.delete(faq)
    await session.flush()


async def count_active_faqs(session: AsyncSession, business_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count()).select_from(Faq).where(Faq.business_id == business_id, Faq.active)
    )
    return result.scalar_one()


async def list_active_faqs(
    session: AsyncSession, business_id: uuid.UUID, limit: int
) -> Sequence[Faq]:
    result = await session.execute(
        business_scoped_select(Faq, business_id)
        .where(Faq.active)
        .order_by(Faq.category.asc().nulls_last(), Faq.created_at, Faq.id)
        .limit(limit)
    )
    return result.scalars().all()


async def search_active_faqs(
    session: AsyncSession, business_id: uuid.UUID, query: str, limit: int
) -> Sequence[Faq]:
    # An all-stopword query makes an empty tsquery: `@@` is then false for every
    # row (Postgres only emits a NOTICE), so the result is [] without erroring.
    stmt = (
        business_scoped_select(Faq, business_id)
        .where(Faq.active, text(f"faqs.search_vector @@ ({_OR_QUERY})"))
        .order_by(
            text(f"ts_rank_cd(faqs.search_vector, ({_OR_QUERY})) DESC"),
            Faq.updated_at.desc(),
            Faq.id,
        )
        .limit(limit)
        .params(q=query)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


class SqlFaqStore:
    """Binds the functions above to one session. Satisfies `services.knowledge.FaqSearchStore`
    and `services.faqs.FaqStore`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_faqs(self, business_id: uuid.UUID) -> Sequence[Faq]:
        return await list_faqs(self._session, business_id)

    async def get_faq(self, business_id: uuid.UUID, faq_id: uuid.UUID) -> Faq | None:
        return await get_faq(self._session, business_id, faq_id)

    async def lock_and_count_faqs(self, business_id: uuid.UUID) -> int:
        return await lock_and_count_faqs(self._session, business_id)

    async def create_faq(
        self,
        business_id: uuid.UUID,
        *,
        question: str,
        answer: str,
        category: str | None,
        active: bool,
    ) -> Faq:
        return await create_faq(
            self._session,
            business_id,
            question=question,
            answer=answer,
            category=category,
            active=active,
        )

    async def save_faq(self, faq: Faq) -> Faq:
        return await save_faq(self._session, faq)

    async def delete_faq(self, faq: Faq) -> None:
        await delete_faq(self._session, faq)

    async def count_active_faqs(self, business_id: uuid.UUID) -> int:
        return await count_active_faqs(self._session, business_id)

    async def list_active_faqs(self, business_id: uuid.UUID, limit: int) -> Sequence[Faq]:
        return await list_active_faqs(self._session, business_id, limit)

    async def search_active_faqs(
        self, business_id: uuid.UUID, query: str, limit: int
    ) -> Sequence[Faq]:
        return await search_active_faqs(self._session, business_id, query, limit)
