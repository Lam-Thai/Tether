"""CORS on real (non-preflight) requests; `tests/api/test_cors.py` covers the preflights."""

import pytest
from httpx import ASGITransport, AsyncClient

import core.config
from core.app import create_app

ALLOWED = "https://allowed.example"
EVIL = "https://evil.example"


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(core.config.settings, "cors_origins", [ALLOWED])
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as c:
        yield c


async def test_a_disallowed_origin_gets_no_cors_headers_on_a_real_request(client):
    response = await client.get("/health", headers={"Origin": EVIL})

    # No allow-origin means the browser blocks the read, whatever else Starlette adds.
    assert "access-control-allow-origin" not in response.headers


async def test_a_disallowed_origin_gets_none_on_an_error_response_either(client):
    response = await client.get(
        "/api/v1/me", headers={"Origin": EVIL, "Authorization": "Bearer nope"}
    )

    assert response.status_code == 401
    assert "access-control-allow-origin" not in response.headers


async def test_an_allowed_origin_is_echoed_exactly_with_credentials(client):
    response = await client.get("/health", headers={"Origin": ALLOWED})

    assert response.headers["access-control-allow-origin"] == ALLOWED
    assert response.headers["access-control-allow-credentials"] == "true"


async def test_a_wildcard_is_never_sent_with_credentials(client):
    for origin in (ALLOWED, EVIL, "null"):
        response = await client.get("/health", headers={"Origin": origin})
        assert response.headers.get("access-control-allow-origin") != "*", origin


async def test_a_lookalike_origin_is_not_allowed(client):
    for origin in (
        ALLOWED + ".evil.example",
        "http://allowed.example",
        "https://allowed.example:8443",
        "https://sub.allowed.example",
    ):
        response = await client.get("/health", headers={"Origin": origin})
        assert "access-control-allow-origin" not in response.headers, origin
