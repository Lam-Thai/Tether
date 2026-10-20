"""Unit tests for `verify_principal` — the module boundary for every Clerk
token failure mode. No FastAPI app, no DB: a fake signing-key resolver
backed by a real RSA keypair, so signature/expiry/issuer checks are real.
"""

from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from core.auth import AuthError, verify_principal
from integrations.clerk.fake import build_fake_signing_key_resolver
from integrations.clerk.resolver import SigningKeyUnavailableError

ISSUER = "https://example.clerk.accounts.dev"


async def _verify(token: str, public_pem: str, **kwargs):
    kwargs.setdefault("issuer", ISSUER)
    kwargs.setdefault("authorized_parties", [])
    return await verify_principal(
        token, resolve_signing_key=build_fake_signing_key_resolver(public_pem), **kwargs
    )


async def test_valid_token_returns_principal(mint_token, rsa_keypair):
    _, public_pem = rsa_keypair
    token = mint_token(subject="user_123", email="owner@example.com", issuer=ISSUER)

    principal = await _verify(token, public_pem)

    assert principal.external_auth_id == "user_123"
    assert principal.email == "owner@example.com"


async def test_expired_token_is_rejected(mint_token, rsa_keypair):
    _, public_pem = rsa_keypair
    token = mint_token(issuer=ISSUER, expires_delta=timedelta(minutes=-5))

    with pytest.raises(AuthError):
        await _verify(token, public_pem)


async def test_wrong_issuer_is_rejected(mint_token, rsa_keypair):
    _, public_pem = rsa_keypair
    token = mint_token(issuer="https://not-the-configured-issuer.example")

    with pytest.raises(AuthError):
        await _verify(token, public_pem)


async def test_tampered_signature_is_rejected(mint_token, rsa_keypair):
    _, public_pem = rsa_keypair
    token = mint_token(issuer=ISSUER)
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}A.{signature}"  # mutate the payload segment

    with pytest.raises(AuthError):
        await _verify(tampered, public_pem)


async def test_token_signed_by_an_untrusted_key_is_rejected(mint_token, rsa_keypair):
    _, public_pem = rsa_keypair
    untrusted_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = mint_token(issuer=ISSUER, signing_key=untrusted_key)

    with pytest.raises(AuthError):
        await _verify(token, public_pem)


async def test_malformed_token_is_rejected():
    with pytest.raises(AuthError):
        await verify_principal(
            "not-a-jwt",
            resolve_signing_key=build_fake_signing_key_resolver("irrelevant"),
            issuer=ISSUER,
            authorized_parties=[],
        )


async def test_unrecognized_authorized_party_is_rejected(mint_token, rsa_keypair):
    _, public_pem = rsa_keypair
    token = mint_token(issuer=ISSUER, azp="https://evil.example")

    with pytest.raises(AuthError):
        await _verify(token, public_pem, authorized_parties=["https://app.example.com"])


async def test_recognized_authorized_party_is_accepted(mint_token, rsa_keypair):
    _, public_pem = rsa_keypair
    token = mint_token(issuer=ISSUER, azp="https://app.example.com")

    principal = await _verify(token, public_pem, authorized_parties=["https://app.example.com"])

    assert principal.external_auth_id


async def test_missing_email_claim_is_rejected(mint_token, rsa_keypair):
    _, public_pem = rsa_keypair
    token = mint_token(issuer=ISSUER, email=None)

    with pytest.raises(AuthError):
        await _verify(token, public_pem)


async def test_unavailable_signing_key_is_rejected(mint_token, rsa_keypair):
    token = mint_token(issuer=ISSUER)

    async def resolve_signing_key(_token: str) -> str:
        raise SigningKeyUnavailableError("JWKS unreachable")

    with pytest.raises(AuthError):
        await verify_principal(
            token, resolve_signing_key=resolve_signing_key, issuer=ISSUER, authorized_parties=[]
        )
