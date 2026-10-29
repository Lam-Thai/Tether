"""`TenantContext`: the only place a request's business identity is resolved.

`get_tenant` is a FastAPI dependency. It verifies the bearer token,
just-in-time upserts the `users` row from the Clerk subject, and resolves
the caller's business from database relationships only (`owner_id`) — never
from a client-supplied `business_id`. Every business-scoped endpoint takes
`Tenant` via this dependency instead of a `business_id` path/query param.
"""

from dataclasses import dataclass

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from core import log_context
from core.auth import AuthError, Principal, verify_principal
from core.config import settings
from core.db import get_session
from integrations.clerk import build_clerk_signing_key_resolver
from integrations.clerk.port import SigningKeyResolver
from models.business import Business
from models.user import User
from repositories.business import get_business_by_owner_id
from repositories.users import get_or_create_user


@dataclass(frozen=True)
class Tenant:
    user: User
    # `None` before the owner has completed onboarding (POST /api/v1/business).
    business: Business | None


def get_bearer_token(authorization: str | None = Header(default=None)) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise AuthError("Missing or malformed Authorization header.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise AuthError("Missing or malformed Authorization header.")
    return token


# One resolver per process — `PyJWKClient` (inside it) caches fetched keys
# itself, so this is built once rather than per-request. Tests override
# `get_signing_key_resolver` instead of touching this module-level value.
_clerk_signing_key_resolver = build_clerk_signing_key_resolver(settings.clerk_jwks_url)


def get_signing_key_resolver() -> SigningKeyResolver:
    return _clerk_signing_key_resolver


async def get_principal(
    token: str = Depends(get_bearer_token),
    resolve_signing_key: SigningKeyResolver = Depends(get_signing_key_resolver),
) -> Principal:
    return await verify_principal(
        token,
        resolve_signing_key=resolve_signing_key,
        issuer=settings.clerk_issuer,
        authorized_parties=settings.clerk_authorized_parties,
    )


async def get_tenant(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Tenant:
    user = await get_or_create_user(
        session, external_auth_id=principal.external_auth_id, email=principal.email
    )
    business = await get_business_by_owner_id(session, owner_id=user.id)
    await session.commit()
    log_context.bind(business_id=business.id if business else None)
    return Tenant(user=user, business=business)
