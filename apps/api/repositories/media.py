"""Repository for `media_objects` and `media_tokens` (short-lived call audio).

Expiry is always judged by the DATABASE clock (`now()`), never the app's.

Tenancy: everything keyed by `cache_key` is scoped by `business_id`, so one business can
never read or overwrite another's cached audio. Lookups keyed by `token_hash`
(`get_live`) are intentionally NOT tenant-scoped: the unguessable token is the capability
(Twilio fetches the URL with no credentials), like the `sms_sid` lookups. Only the SHA-256 of
a token is ever stored. The service layer decides what a token looks like; this module only
sees 32-byte hashes.
"""

import uuid
from datetime import timedelta

from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.media import MediaObject, MediaToken


def _in(seconds: int):
    return func.now() + timedelta(seconds=seconds)


async def store(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    data: bytes,
    content_type: str,
    cache_key: str | None,
    object_ttl_seconds: int,
    token_hash: bytes,
    token_ttl_seconds: int,
) -> None:
    """Insert the object (upsert on `(business_id, cache_key)` when keyed) and one token.

    Racing writers of the same key both succeed: the later one replaces the data, and each
    gets its own token for the one row. The caller commits.
    """
    statement = insert(MediaObject).values(
        business_id=business_id,
        data=data,
        content_type=content_type,
        cache_key=cache_key,
        expires_at=_in(object_ttl_seconds),
    )
    if cache_key is not None:
        statement = statement.on_conflict_do_update(
            index_elements=[MediaObject.business_id, MediaObject.cache_key],
            index_where=MediaObject.cache_key.is_not(None),
            set_={
                "data": statement.excluded.data,
                "content_type": statement.excluded.content_type,
                "expires_at": statement.excluded.expires_at,
            },
        )
    object_id = (await session.execute(statement.returning(MediaObject.id))).scalar_one()
    await session.execute(
        insert(MediaToken).values(
            token_hash=token_hash,
            object_id=object_id,
            business_id=business_id,
            expires_at=_in(token_ttl_seconds),
        )
    )


async def mint_for_cached(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    cache_key: str,
    token_hash: bytes,
    token_ttl_seconds: int,
) -> bool:
    """Mint a token for this business's LIVE object under `cache_key`. False if none."""
    live = (
        select(MediaObject.id)
        .where(
            MediaObject.business_id == business_id,
            MediaObject.cache_key == cache_key,
            MediaObject.expires_at > func.now(),
        )
        .scalar_subquery()
    )
    # A NULL object_id (no live object) violates NOT NULL, so use INSERT ... SELECT instead.
    candidate = select(
        literal(token_hash, MediaToken.token_hash.type),
        MediaObject.id,
        MediaObject.business_id,
        _in(token_ttl_seconds),
    ).where(MediaObject.id == live)
    result = await session.execute(
        insert(MediaToken)
        .from_select(["token_hash", "object_id", "business_id", "expires_at"], candidate)
        .on_conflict_do_nothing()
        .returning(MediaToken.token_hash)
    )
    return result.first() is not None


async def get_live(session: AsyncSession, token_hash: bytes) -> tuple[str, bytes] | None:
    """`(content_type, data)` for a token that is unexpired AND whose object is unexpired."""
    row = (
        await session.execute(
            select(MediaObject.content_type, MediaObject.data)
            .join(MediaToken, MediaToken.object_id == MediaObject.id)
            .where(
                MediaToken.token_hash == token_hash,
                MediaToken.expires_at > func.now(),
                MediaObject.expires_at > func.now(),
            )
        )
    ).first()
    return None if row is None else (row.content_type, row.data)


async def purge_expired(session: AsyncSession, *, limit: int) -> int:
    """Delete up to `limit` expired tokens and `limit` expired objects (cascading to their
    tokens). Returns how many rows were deleted directly. The caller commits."""
    stale_tokens = (
        select(MediaToken.token_hash)
        .where(MediaToken.expires_at <= func.now())
        .order_by(MediaToken.expires_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    tokens = await session.execute(
        delete(MediaToken).where(MediaToken.token_hash.in_(stale_tokens))
    )
    stale_objects = (
        select(MediaObject.id)
        .where(MediaObject.expires_at <= func.now())
        .order_by(MediaObject.expires_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    objects = await session.execute(delete(MediaObject).where(MediaObject.id.in_(stale_objects)))
    return int(getattr(tokens, "rowcount", 0) or 0) + int(getattr(objects, "rowcount", 0) or 0)
