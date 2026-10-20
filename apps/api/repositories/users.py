"""Repository for `users`. Identity rows, not tenant-scoped."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User


async def get_user_by_external_auth_id(session: AsyncSession, external_auth_id: str) -> User | None:
    result = await session.execute(select(User).where(User.external_auth_id == external_auth_id))
    return result.scalar_one_or_none()


async def create_user(session: AsyncSession, *, external_auth_id: str, email: str) -> User:
    user = User(external_auth_id=external_auth_id, email=email)
    session.add(user)
    await session.flush()
    return user


async def get_or_create_user(session: AsyncSession, *, external_auth_id: str, email: str) -> User:
    """Just-in-time provisioning: the first authenticated request for a
    Clerk account creates its `users` row. Never called from a webhook —
    there isn't one for this slice.

    Inserts and ignores a conflict on `external_auth_id` in one statement
    (instead of look-then-create) so two concurrent requests for the same
    new Clerk account both land on the same row instead of one raising a
    uniqueness error.
    """
    insert_stmt = (
        pg_insert(User)
        .values(external_auth_id=external_auth_id, email=email)
        .on_conflict_do_nothing(index_elements=[User.external_auth_id])
    )
    await session.execute(insert_stmt)
    user = await get_user_by_external_auth_id(session, external_auth_id)
    assert user is not None
    return user
