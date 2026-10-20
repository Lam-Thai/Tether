"""Clerk principal verification.

`verify_principal` is the only function in the app that knows a Clerk
session token's claim shape (`sub`, `email`, `azp`) or its signature/expiry/
issuer/authorized-party rules. Every failure mode — bad signature, expired,
wrong issuer, unrecognized `azp`, unreachable JWKS — maps to the same
`AuthError` (401), so nothing downstream has to distinguish them.
"""

from dataclasses import dataclass

import jwt
from jwt.exceptions import InvalidTokenError

from core.errors import AppError
from integrations.clerk.port import SigningKeyResolver
from integrations.clerk.resolver import SigningKeyUnavailableError


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, as Clerk described them. Nothing below
    `core.tenant` ever sees a raw Clerk claim — this is the boundary."""

    external_auth_id: str
    email: str


async def verify_principal(
    token: str,
    *,
    resolve_signing_key: SigningKeyResolver,
    issuer: str,
    authorized_parties: list[str],
) -> Principal:
    try:
        signing_key = await resolve_signing_key(token)
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"require": ["exp", "iss", "sub"]},
        )
    except (SigningKeyUnavailableError, InvalidTokenError) as exc:
        raise AuthError("Session token could not be verified.") from exc

    if authorized_parties and payload.get("azp") not in authorized_parties:
        raise AuthError("Session token was issued to an unrecognized party.")

    email = payload.get("email")
    if not isinstance(email, str) or not email:
        raise AuthError("Session token is missing an email claim.")

    return Principal(external_auth_id=payload["sub"], email=email)
