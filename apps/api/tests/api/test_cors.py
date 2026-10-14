import pytest
from httpx import ASGITransport, AsyncClient

import core.config
from core.app import create_app


@pytest.fixture
def restricted_app(monkeypatch):
    monkeypatch.setattr(core.config.settings, "cors_origins", ["https://allowed.example"])
    return create_app()


async def test_disallowed_origin_preflight_is_rejected(restricted_app):
    transport = ASGITransport(app=restricted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/health",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    # Starlette's CORSMiddleware responds to a disallowed preflight origin
    # with a 400 "Disallowed CORS" response (observed behavior).
    assert response.status_code == 400
    assert "Disallowed CORS" in response.text
    assert "access-control-allow-origin" not in response.headers


async def test_allowed_origin_preflight_is_accepted(restricted_app):
    transport = ASGITransport(app=restricted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/health",
            headers={
                "Origin": "https://allowed.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://allowed.example"
