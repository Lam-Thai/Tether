"""Repository for `users`. Identity rows, not tenant-scoped."""

from sqlalchemy import select
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
    """
    existing = await get_user_by_external_auth_id(session, external_auth_id)
    if existing is not None:
        return existing
    return await create_user(session, external_auth_id=external_auth_id, email=email)
