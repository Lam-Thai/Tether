"""Dependencies for the FAQ routes (and anything else that needs the FAQ store)."""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db import get_session
from repositories.faqs import SqlFaqStore


def get_faq_store(session: AsyncSession = Depends(get_session)) -> SqlFaqStore:
    return SqlFaqStore(session)


def get_faq_max() -> int:
    """The per-business FAQ cap; a dependency so tests can lower it."""
    return settings.faq_max_per_business
