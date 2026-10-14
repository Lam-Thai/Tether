"""Async SQLAlchemy engine + session factory.

Hides serverless-safe connection handling so callers never think about
pooling: build one engine per process, one session per request.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.config import settings


def _normalize_database_url(raw_url: str) -> str:
    # Neon's connection-string UI hands out a plain `postgresql://` URL with
    # no driver specified; SQLAlchemy's async engine needs an explicit async
    # driver, so rewrite it to use asyncpg when one isn't already present.
    if raw_url.startswith("postgresql://"):
        return raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw_url


engine = create_async_engine(
    _normalize_database_url(settings.database_url),
    poolclass=NullPool,  # Neon's own pooler handles pooling at the proxy layer.
    pool_pre_ping=True,
    connect_args={"server_settings": {"statement_timeout": "5000"}},
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield one session per request, close it after."""
    async with async_session_factory() as session:
        yield session
