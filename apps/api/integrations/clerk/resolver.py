"""Real adapter: fetches and caches Clerk's JWKS.

`PyJWKClient` does its own in-memory signing-key cache (keyed by `kid`), so
this module only has to adapt its sync, blocking API to the async
`SigningKeyResolver` port and translate lookup failures into
`SigningKeyUnavailableError` — the one exception `core.auth` knows how to
map to a 401.
"""

import asyncio

from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

from integrations.clerk.port import SigningKeyResolver


class SigningKeyUnavailableError(Exception):
    """The signing key for a token's `kid` could not be fetched."""


def build_clerk_signing_key_resolver(jwks_url: str) -> SigningKeyResolver:
    jwk_client = PyJWKClient(jwks_url)

    async def resolve(token: str) -> str:
        try:
            signing_key = await asyncio.to_thread(jwk_client.get_signing_key_from_jwt, token)
        except PyJWKClientError as exc:
            raise SigningKeyUnavailableError(str(exc)) from exc
        return signing_key.key

    return resolve
