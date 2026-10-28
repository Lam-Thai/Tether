"""`MediaStore` over an in-memory repository."""

import hashlib
import logging
import re
import uuid

import pytest

from services.media import MediaObject, MediaStore
from tests.services.memory_media_repo import MemoryMediaRepo

TOKEN_TTL = 300


@pytest.fixture
def repo() -> MemoryMediaRepo:
    return MemoryMediaRepo()


@pytest.fixture
def store(repo) -> MediaStore:
    return MediaStore(repo, token_ttl_seconds=TOKEN_TTL)


async def test_put_then_get_round_trips(store):
    token = await store.put(uuid.uuid4(), b"audio", "audio/mpeg", ttl_seconds=60)

    media = await store.get(token)

    assert media == MediaObject(content_type="audio/mpeg", data=b"audio")


async def test_token_is_43_url_safe_chars_and_distinct(store):
    business = uuid.uuid4()
    tokens = {await store.put(business, b"x", "audio/mpeg", ttl_seconds=60) for _ in range(200)}

    assert len(tokens) == 200
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{43}", t) for t in tokens)


async def test_only_the_hash_is_stored(store, repo):
    token = await store.put(uuid.uuid4(), b"x", "audio/mpeg", ttl_seconds=60)

    assert list(repo.tokens) == [hashlib.sha256(token.encode()).digest()]


@pytest.mark.parametrize(
    "token",
    ["", "short", "a" * 42, "a" * 44, "a" * 42 + "=", "a" * 42 + "/", "a" * 42 + "\n", "é" * 43],
    ids=["empty", "short", "42", "44", "padding", "slash", "newline", "non-ascii"],
)
async def test_malformed_token_makes_no_repo_call(store, repo, token):
    assert await store.get(token) is None
    assert repo.calls == []


async def test_unknown_token_is_none(store):
    assert await store.get("a" * 43) is None


async def test_token_expires_but_longer_lived_object_can_be_reminted(store, repo):
    business = uuid.uuid4()
    token = await store.put(business, b"x", "audio/mpeg", ttl_seconds=3600, cache_key="k")

    repo.now = TOKEN_TTL + 1

    assert await store.get(token) is None
    fresh = await store.cached(business, "k")
    assert fresh is not None
    assert (await store.get(fresh)) is not None


async def test_object_expiry_is_at_least_the_token_ttl(store, repo):
    token = await store.put(uuid.uuid4(), b"x", "audio/mpeg", ttl_seconds=10)

    repo.now = TOKEN_TTL - 1

    assert await store.get(token) is not None


async def test_expired_object_is_not_cached(store, repo):
    business = uuid.uuid4()
    await store.put(business, b"x", "audio/mpeg", ttl_seconds=TOKEN_TTL, cache_key="k")
    repo.now = TOKEN_TTL + 1

    assert await store.cached(business, "k") is None


async def test_cached_returns_a_different_fresh_token_for_the_same_object(store):
    business = uuid.uuid4()
    first = await store.put(business, b"x", "audio/mpeg", ttl_seconds=600, cache_key="k")

    second = await store.cached(business, "k")

    assert second is not None and second != first
    assert (await store.get(second)) == (await store.get(first))


async def test_cache_is_isolated_between_businesses(store):
    a, b = uuid.uuid4(), uuid.uuid4()
    await store.put(a, b"a-audio", "audio/mpeg", ttl_seconds=600, cache_key="k")

    assert await store.cached(b, "k") is None
    token_b = await store.put(b, b"b-audio", "audio/mpeg", ttl_seconds=600, cache_key="k")
    token_a = await store.cached(a, "k")
    assert token_a is not None
    media_a, media_b = await store.get(token_a), await store.get(token_b)
    assert media_a is not None and media_a.data == b"a-audio"
    assert media_b is not None and media_b.data == b"b-audio"


async def test_same_cache_key_replaces_the_object(store, repo):
    business = uuid.uuid4()
    await store.put(business, b"old", "audio/mpeg", ttl_seconds=600, cache_key="k")
    await store.put(business, b"new", "audio/wav", ttl_seconds=600, cache_key="k")

    token = await store.cached(business, "k")

    assert token is not None
    assert await store.get(token) == MediaObject("audio/wav", b"new")
    assert len(repo.objects) == 1


async def test_put_purges_expired_rows(store, repo):
    business = uuid.uuid4()
    await store.put(business, b"x", "audio/mpeg", ttl_seconds=10)
    repo.now = TOKEN_TTL + 1

    await store.put(business, b"y", "audio/mpeg", ttl_seconds=10)

    assert len(repo.objects) == 1 and len(repo.tokens) == 1


async def test_purge_failure_is_swallowed_and_logged_as_type_only(store, repo, caplog):
    repo.fail_purge = True

    with caplog.at_level(logging.WARNING, logger="tether.media"):
        token = await store.put(uuid.uuid4(), b"x", "audio/mpeg", ttl_seconds=60)

    assert await store.get(token) is not None
    assert "RuntimeError" in caplog.text
    assert "secret-token" not in caplog.text


def test_repr_does_not_dump_bytes():
    assert "secretbytes" not in repr(MediaObject("audio/mpeg", b"secretbytes"))
