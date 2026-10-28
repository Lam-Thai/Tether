"""`MediaStore`: short-lived, token-addressed blobs (the audio Twilio plays on a call).

A deep module: callers `put` bytes and get back an unguessable token to put in a URL;
`get` turns a token back into bytes while it is live. Only `sha256(token)` is stored, so a
database read never yields a working URL, and every token expires within `token_ttl_seconds`
even when the object itself is cached longer (`cache_key`). Expiry is judged by the database
clock inside the repository. Tokens and bytes are never logged.
"""

import hashlib
import logging
import re
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger("tether.media")

PURGE_BATCH = 100
# `secrets.token_urlsafe(32)`: 32 random bytes, unpadded base64url = 43 characters.
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{43}")


@dataclass(frozen=True)
class MediaObject:
    content_type: str
    data: bytes = field(repr=False)


class MediaRepository(Protocol):
    async def store(
        self,
        *,
        business_id: uuid.UUID,
        data: bytes,
        content_type: str,
        cache_key: str | None,
        object_ttl_seconds: int,
        token_hash: bytes,
        token_ttl_seconds: int,
    ) -> None: ...

    async def mint_for_cached(
        self,
        *,
        business_id: uuid.UUID,
        cache_key: str,
        token_hash: bytes,
        token_ttl_seconds: int,
    ) -> bool: ...

    async def get_live(self, token_hash: bytes) -> MediaObject | None: ...

    async def purge_expired(self, *, limit: int) -> int: ...


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


class MediaStore:
    def __init__(self, repo: MediaRepository, *, token_ttl_seconds: int) -> None:
        self._repo = repo
        self._token_ttl = token_ttl_seconds

    async def put(
        self,
        business_id: uuid.UUID,
        data: bytes,
        content_type: str,
        *,
        ttl_seconds: int,
        cache_key: str | None = None,
    ) -> str:
        """Store `data` and return a token valid for `token_ttl_seconds`.

        The object lives `max(ttl_seconds, token_ttl_seconds)`. With `cache_key` it replaces
        any earlier object for that `(business, key)`."""
        token = _new_token()
        await self._repo.store(
            business_id=business_id,
            data=data,
            content_type=content_type,
            cache_key=cache_key,
            object_ttl_seconds=max(ttl_seconds, self._token_ttl),
            token_hash=_hash(token),
            token_ttl_seconds=self._token_ttl,
        )
        await self._purge()
        return token

    async def cached(self, business_id: uuid.UUID, cache_key: str) -> str | None:
        """A fresh short-lived token for this business's live cached object, else None."""
        token = _new_token()
        found = await self._repo.mint_for_cached(
            business_id=business_id,
            cache_key=cache_key,
            token_hash=_hash(token),
            token_ttl_seconds=self._token_ttl,
        )
        # Cache hits are most call lines, so they purge too: expired audio doesn't wait for
        # the next synthesis to leave the database.
        await self._purge()
        return token if found else None

    async def get(self, token: str) -> MediaObject | None:
        """The object for a live token; None if malformed, unknown or expired."""
        if not _TOKEN_SHAPE.fullmatch(token):
            return None
        return await self._repo.get_live(_hash(token))

    async def _purge(self) -> None:
        try:
            await self._repo.purge_expired(limit=PURGE_BATCH)
        except Exception as exc:  # best effort: never fail a put over housekeeping
            logger.warning("media purge failed: %s", type(exc).__name__)
