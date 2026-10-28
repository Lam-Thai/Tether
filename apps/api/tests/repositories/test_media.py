"""`repositories/media.py` and `SqlMediaRepository` on real Postgres."""

import asyncio
import hashlib
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from core.db import async_session_factory
from core.media import SqlMediaRepository
from models.business import Business
from models.media import MediaObject, MediaToken
from services.media import MediaStore

TTL = 300


def _store() -> MediaStore:
    return MediaStore(SqlMediaRepository(async_session_factory), token_ttl_seconds=TTL)


def _h(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


async def _expire(business_id, *, tokens: bool = False, objects: bool = False) -> None:
    async with async_session_factory() as session:
        for flag, table in ((tokens, "media_tokens"), (objects, "media_objects")):
            if flag:
                await session.execute(
                    text(
                        f"UPDATE {table} SET expires_at = now() - interval '1 second' "
                        "WHERE business_id = :b"
                    ),
                    {"b": business_id},
                )
        await session.commit()


async def _count(model, business_id) -> int:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(func.count()).select_from(model).where(model.business_id == business_id)
            )
        ).scalar_one()


async def test_round_trip_and_hash_only_stored(two_tenants):
    a, _ = two_tenants
    token = await _store().put(a.business_id, b"audio", "audio/mpeg", ttl_seconds=60)

    media = await _store().get(token)

    assert media is not None and media.data == b"audio" and media.content_type == "audio/mpeg"
    async with async_session_factory() as session:
        hashes = (
            (
                await session.execute(
                    select(MediaToken.token_hash).where(MediaToken.business_id == a.business_id)
                )
            )
            .scalars()
            .all()
        )
    assert hashes == [_h(token)]


async def test_expired_token_returns_none(two_tenants):
    a, _ = two_tenants
    token = await _store().put(a.business_id, b"x", "audio/mpeg", ttl_seconds=3600)
    await _expire(a.business_id, tokens=True)

    assert await _store().get(token) is None


async def test_expired_object_behind_live_token_returns_none(two_tenants):
    a, _ = two_tenants
    token = await _store().put(a.business_id, b"x", "audio/mpeg", ttl_seconds=60)
    await _expire(a.business_id, objects=True)

    assert await _store().get(token) is None


async def test_cache_key_upsert_keeps_one_row_and_replaces_data(two_tenants):
    a, _ = two_tenants
    await _store().put(a.business_id, b"old", "audio/mpeg", ttl_seconds=600, cache_key="k")
    token = await _store().put(a.business_id, b"new", "audio/wav", ttl_seconds=600, cache_key="k")

    assert await _count(MediaObject, a.business_id) == 1
    media = await _store().get(token)
    assert media is not None and (media.data, media.content_type) == (b"new", "audio/wav")


async def test_expired_cache_row_is_overwritten_by_put(two_tenants):
    a, _ = two_tenants
    await _store().put(a.business_id, b"old", "audio/mpeg", ttl_seconds=600, cache_key="k")
    await _expire(a.business_id, objects=True, tokens=True)
    assert await _store().cached(a.business_id, "k") is None

    token = await _store().put(a.business_id, b"new", "audio/mpeg", ttl_seconds=600, cache_key="k")

    media = await _store().get(token)
    assert media is not None and media.data == b"new"


async def test_racing_cache_writers_both_succeed(two_tenants):
    a, _ = two_tenants
    tokens = await asyncio.gather(
        *(
            _store().put(a.business_id, bytes([i]), "audio/mpeg", ttl_seconds=600, cache_key="k")
            for i in range(5)
        )
    )

    assert await _count(MediaObject, a.business_id) == 1
    for token in tokens:
        assert await _store().get(token) is not None


async def test_cached_gives_fresh_token_for_live_object(two_tenants):
    a, _ = two_tenants
    first = await _store().put(a.business_id, b"x", "audio/mpeg", ttl_seconds=600, cache_key="k")

    second = await _store().cached(a.business_id, "k")

    assert second is not None and second != first
    media = await _store().get(second)
    assert media is not None and media.data == b"x"


async def test_cached_never_returns_another_tenants_object(two_tenants):
    a, b = two_tenants
    await _store().put(a.business_id, b"a-only", "audio/mpeg", ttl_seconds=600, cache_key="same")

    assert await _store().cached(b.business_id, "same") is None
    # B writing the same key creates its own row; A's data is untouched.
    token_b = await _store().put(
        b.business_id, b"b", "audio/mpeg", ttl_seconds=600, cache_key="same"
    )
    token_a = await _store().cached(a.business_id, "same")
    assert token_a is not None
    media_a = await _store().get(token_a)
    media_b = await _store().get(token_b)
    assert media_a is not None and media_a.data == b"a-only"
    assert media_b is not None and media_b.data == b"b"


async def test_purge_removes_expired_and_keeps_live(two_tenants):
    a, b = two_tenants
    await _store().put(a.business_id, b"dead", "audio/mpeg", ttl_seconds=60)
    live = await _store().put(b.business_id, b"live", "audio/mpeg", ttl_seconds=60)
    await _expire(a.business_id, objects=True, tokens=True)

    # Parallel workers share the table, so loop until this tenant's stale rows are gone.
    repo = SqlMediaRepository(async_session_factory)
    for _ in range(20):
        await repo.purge_expired(limit=100)
        if await _count(MediaObject, a.business_id) == 0:
            break

    assert await _count(MediaObject, a.business_id) == 0
    assert await _count(MediaToken, a.business_id) == 0
    assert await _count(MediaObject, b.business_id) == 1
    assert await _store().get(live) is not None


async def test_deleting_business_cascades_to_media(app, mint_token):
    external = f"user_{uuid.uuid4().hex}"
    token = mint_token(subject=external, email=f"{external}@example.com")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/business",
            json={"name": "Cascade", "industry": "Salon", "address": "1 Main", "timezone": "UTC"},
            headers={"Authorization": f"Bearer {token}"},
        )
    business_id = uuid.UUID(response.json()["id"])
    await _store().put(business_id, b"x", "audio/mpeg", ttl_seconds=60)

    async with async_session_factory() as session:
        await session.execute(delete(Business).where(Business.id == business_id))
        await session.execute(
            text("DELETE FROM users WHERE external_auth_id = :e"), {"e": external}
        )
        await session.commit()

    assert await _count(MediaObject, business_id) == 0
    assert await _count(MediaToken, business_id) == 0


@pytest.mark.parametrize(
    ("content_type", "data"),
    [("", b"x"), ("audio/mpeg", b"x" * (5 * 1024 * 1024 + 1))],
    ids=["empty-content-type", "too-large"],
)
async def test_constraints_reject_bad_rows(two_tenants, content_type, data):
    a, _ = two_tenants
    async with async_session_factory() as session:
        session.add(
            MediaObject(
                business_id=a.business_id,
                content_type=content_type,
                data=data,
                expires_at=func.now(),
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
