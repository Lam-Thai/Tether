"""Integration tests against a real Postgres (DATABASE_URL from the env).

These are deliberately not skipped when the DB is unreachable: a hung or
missing DATABASE_URL should fail loudly here, not pass silently.
"""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.db import async_session_factory
from repositories.health import check_db_connection


async def test_check_db_connection_returns_true_for_reachable_db():
    async with async_session_factory() as session:
        ok = await check_db_connection(session)

    assert ok is True


async def test_check_db_connection_returns_false_for_unreachable_db():
    # Point at a connection that cannot connect (bogus port) instead of a
    # real DB to simulate a down/unreachable database.
    bad_engine = create_async_engine(
        "postgresql+asyncpg://postgres:postgres@localhost:1/does-not-exist"
    )
    bad_session_factory = async_sessionmaker(bad_engine, expire_on_commit=False)

    try:
        async with bad_session_factory() as session:
            ok = await check_db_connection(session, timeout_seconds=1.0)
    finally:
        await bad_engine.dispose()

    assert ok is False
