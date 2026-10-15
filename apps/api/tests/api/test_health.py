"""Integration tests for `GET /health` against a real Postgres.

Covers both outcomes: a healthy DB (200, db: connected) and a simulated DB
failure (503, db: error) via a dependency override pointed at an
unreachable database, rather than tearing down the app's real engine.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.app import create_app
from core.db import get_session


@pytest.fixture
def app():
    return create_app()


async def test_health_endpoint_returns_200_when_db_connected(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "connected"
    assert "version" in body


async def test_health_endpoint_returns_503_when_db_unreachable(app):
    bad_engine = create_async_engine(
        "postgresql+asyncpg://postgres:postgres@localhost:1/does-not-exist"
    )
    bad_session_factory = async_sessionmaker(bad_engine, expire_on_commit=False)

    async def bad_get_session():
        async with bad_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = bad_get_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
    finally:
        app.dependency_overrides.clear()
        await bad_engine.dispose()

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["db"] == "error"
