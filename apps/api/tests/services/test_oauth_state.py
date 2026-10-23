import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from services.oauth_state import AUDIENCE, InvalidOAuthStateError, OAuthStateSigner, code_challenge

SECRET = "s" * 40
NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
BIZ, USER = uuid.uuid4(), uuid.uuid4()


def signer(now: datetime = NOW, secret: str = SECRET) -> OAuthStateSigner:
    return OAuthStateSigner(secret, 600, clock=lambda: now)


def test_valid_state_returns_the_pkce_verifier_issued_with_it():
    issued = signer().issue(BIZ, USER)

    verifier = signer().verify(issued.state, business_id=BIZ, user_id=USER)

    assert verifier == issued.code_verifier
    assert len(verifier) >= 43
    assert code_challenge(verifier) != verifier


def test_each_state_has_its_own_verifier():
    first, second = signer().issue(BIZ, USER), signer().issue(BIZ, USER)
    assert first.code_verifier != second.code_verifier


def test_tampered_or_foreign_signature_is_rejected():
    issued = signer().issue(BIZ, USER)
    with pytest.raises(InvalidOAuthStateError):
        signer().verify(issued.state[:-3] + "abc", business_id=BIZ, user_id=USER)
    with pytest.raises(InvalidOAuthStateError):
        signer(secret="t" * 40).verify(issued.state, business_id=BIZ, user_id=USER)
    with pytest.raises(InvalidOAuthStateError):
        signer().verify("not-a-jwt", business_id=BIZ, user_id=USER)


def test_expired_state_is_rejected_by_the_injected_clock():
    issued = signer().issue(BIZ, USER)
    signer(NOW + timedelta(seconds=599)).verify(issued.state, business_id=BIZ, user_id=USER)
    with pytest.raises(InvalidOAuthStateError):
        signer(NOW + timedelta(seconds=601)).verify(issued.state, business_id=BIZ, user_id=USER)


def test_state_from_another_business_or_user_is_rejected():
    issued = signer().issue(BIZ, USER)
    with pytest.raises(InvalidOAuthStateError):
        signer().verify(issued.state, business_id=uuid.uuid4(), user_id=USER)
    with pytest.raises(InvalidOAuthStateError):
        signer().verify(issued.state, business_id=BIZ, user_id=uuid.uuid4())


def test_wrong_audience_or_missing_nonce_is_rejected():
    claims = {
        "sub": str(BIZ),
        "uid": str(USER),
        "nonce": "n",
        "iat": NOW,
        "exp": NOW + timedelta(minutes=5),
    }
    wrong_aud = jwt.encode({**claims, "aud": "other"}, SECRET, algorithm="HS256")
    no_nonce = jwt.encode(
        {k: v for k, v in claims.items() if k != "nonce"} | {"aud": AUDIENCE},
        SECRET,
        algorithm="HS256",
    )
    for state in (wrong_aud, no_nonce):
        with pytest.raises(InvalidOAuthStateError):
            signer().verify(state, business_id=BIZ, user_id=USER)
