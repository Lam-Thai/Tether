"""In-memory fake of the signing-key port, for tests.

Returns one fixed PEM key for every token instead of fetching a JWKS, so
`core.auth.verify_principal` can be exercised against real RS256-signed
tokens (expired, wrong issuer, tampered, ...) without any network call.
"""

from integrations.clerk.port import SigningKeyResolver


def build_fake_signing_key_resolver(public_key_pem: str) -> SigningKeyResolver:
    async def resolve(token: str) -> str:
        return public_key_pem

    return resolve
