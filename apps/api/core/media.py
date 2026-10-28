"""Wires `MediaStore` to Postgres.

`SqlMediaRepository` binds a session FACTORY: each operation is its own committed
transaction, so a token is visible to Twilio before the webhook's request transaction ends
(and no transaction is held across a provider call). It lives in `core` because it speaks
`services.media.MediaObject`. Tests override `get_media_store`.

`core.voice` is not imported here (it pulls in most of the app and would risk a cycle); this
uses `core.db.async_session_factory`, the same factory `core.voice.get_session_factory`
returns.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import settings
from core.db import async_session_factory
from repositories import media as media_repo
from services.media import MediaObject, MediaStore

# Public path of the token-addressed media route; the TwiML renderer builds
# `{public origin}{MEDIA_PATH}/{token}`.
MEDIA_PATH = "/api/v1/media"


class SqlMediaRepository:
    """`services.media.MediaRepository` over Postgres; one committed transaction per call."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

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
    ) -> None:
        async with self._sessions() as session:
            await media_repo.store(
                session,
                business_id=business_id,
                data=data,
                content_type=content_type,
                cache_key=cache_key,
                object_ttl_seconds=object_ttl_seconds,
                token_hash=token_hash,
                token_ttl_seconds=token_ttl_seconds,
            )
            await session.commit()

    async def mint_for_cached(
        self,
        *,
        business_id: uuid.UUID,
        cache_key: str,
        token_hash: bytes,
        token_ttl_seconds: int,
    ) -> bool:
        async with self._sessions() as session:
            minted = await media_repo.mint_for_cached(
                session,
                business_id=business_id,
                cache_key=cache_key,
                token_hash=token_hash,
                token_ttl_seconds=token_ttl_seconds,
            )
            await session.commit()
            return minted

    async def get_live(self, token_hash: bytes) -> MediaObject | None:
        async with self._sessions() as session:
            row = await media_repo.get_live(session, token_hash)
        return None if row is None else MediaObject(content_type=row[0], data=row[1])

    async def purge_expired(self, *, limit: int) -> int:
        async with self._sessions() as session:
            removed = await media_repo.purge_expired(session, limit=limit)
            await session.commit()
            return removed


def get_media_store() -> MediaStore:
    return MediaStore(
        SqlMediaRepository(async_session_factory),
        token_ttl_seconds=settings.media_token_ttl_seconds,
    )
