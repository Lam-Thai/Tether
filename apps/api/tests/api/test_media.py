"""`GET`/`HEAD /api/v1/media/{token}` on real Postgres."""

import secrets

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from core.db import async_session_factory
from core.media import MEDIA_PATH, SqlMediaRepository
from services.media import MediaStore


def _store() -> MediaStore:
    return MediaStore(SqlMediaRepository(async_session_factory), token_ttl_seconds=300)


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_serves_bytes_with_headers(client, two_tenants):
    a, _ = two_tenants
    token = await _store().put(a.business_id, b"RIFFaudio", "audio/wav", ttl_seconds=60)

    response = await client.get(f"{MEDIA_PATH}/{token}")

    assert response.status_code == 200
    assert response.content == b"RIFFaudio"
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_head_has_length_and_no_body(client, two_tenants):
    a, _ = two_tenants
    token = await _store().put(a.business_id, b"12345678", "audio/mpeg", ttl_seconds=60)

    response = await client.head(f"{MEDIA_PATH}/{token}")

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == "8"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["cache-control"] == "no-store"


async def test_expired_token_is_404_envelope(client, two_tenants):
    a, _ = two_tenants
    token = await _store().put(a.business_id, b"x", "audio/mpeg", ttl_seconds=60)
    async with async_session_factory() as session:
        await session.execute(
            text(
                "UPDATE media_tokens SET expires_at = now() - interval '1 second' "
                "WHERE business_id = :b"
            ),
            {"b": a.business_id},
        )
        await session.commit()

    response = await client.get(f"{MEDIA_PATH}/{token}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "media_not_found"


async def test_all_misses_look_the_same(client):
    unknown = await client.get(f"{MEDIA_PATH}/{secrets.token_urlsafe(32)}")
    malformed = await client.get(f"{MEDIA_PATH}/not-a-token")

    assert unknown.status_code == malformed.status_code == 404
    assert unknown.json() == malformed.json()
    assert unknown.json()["error"]["code"] == "media_not_found"


async def test_head_miss_is_404(client):
    response = await client.head(f"{MEDIA_PATH}/{secrets.token_urlsafe(32)}")

    assert response.status_code == 404


async def test_not_in_openapi_and_no_listing(client):
    schema = (await client.get("/openapi.json")).json()
    assert not any("/media" in path for path in schema["paths"])
    assert (await client.get(MEDIA_PATH)).status_code in (404, 405)
