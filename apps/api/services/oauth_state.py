"""`OAuthStateSigner`: the `state` parameter of the Google OAuth round trip.

State is a short-lived HS256 JWT bound to the business and the user who started the
flow. The web callback page sends it back with the owner's bearer token, and `verify`
refuses a state whose business or user differs from that bearer's (login CSRF: an
attacker cannot plant their own authorization on a victim's account).

PKCE needs no server-side state either: the code verifier is an HMAC of the state's
nonce under the same secret, so `issue` and `verify` derive it identically and only
the S256 challenge ever leaves the server before the code exchange. No FastAPI or
SQLAlchemy imports.
"""

import base64
import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from core.errors import AppError

AUDIENCE = "tether:calendar-oauth"


class InvalidOAuthStateError(AppError):
    status_code = 400
    code = "invalid_oauth_state"

    def __init__(self) -> None:
        super().__init__("This connection attempt is invalid or has expired. Please try again.")


@dataclass(frozen=True)
class IssuedState:
    state: str
    code_verifier: str


def code_challenge(code_verifier: str) -> str:
    return _b64(hashlib.sha256(code_verifier.encode()).digest())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OAuthStateSigner:
    def __init__(
        self, secret: str, ttl_seconds: int, clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self._secret = secret
        self._ttl = ttl_seconds
        self._clock = clock

    def issue(self, business_id: uuid.UUID, user_id: uuid.UUID) -> IssuedState:
        nonce = secrets.token_urlsafe(16)
        now = self._clock()
        claims = {
            "aud": AUDIENCE,
            "sub": str(business_id),
            "uid": str(user_id),
            "nonce": nonce,
            "iat": now,
            "exp": now + timedelta(seconds=self._ttl),
        }
        state = jwt.encode(claims, self._secret, algorithm="HS256")
        return IssuedState(state=state, code_verifier=self._verifier(nonce))

    def verify(self, state: str, *, business_id: uuid.UUID, user_id: uuid.UUID) -> str:
        """Returns the PKCE code verifier, or raises `InvalidOAuthStateError`."""
        try:
            claims = jwt.decode(
                state,
                self._secret,
                algorithms=["HS256"],
                audience=AUDIENCE,
                options={
                    "require": ["exp", "iat", "aud", "sub"],
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
        except jwt.PyJWTError:
            raise InvalidOAuthStateError from None
        # The injectable clock decides expiry (PyJWT would read the real time).
        exp, nonce = claims.get("exp"), claims.get("nonce")
        if not isinstance(exp, int | float) or not isinstance(nonce, str):
            raise InvalidOAuthStateError
        if self._clock() >= datetime.fromtimestamp(exp, UTC):
            raise InvalidOAuthStateError
        if claims["sub"] != str(business_id) or claims.get("uid") != str(user_id):
            raise InvalidOAuthStateError
        return self._verifier(nonce)

    def _verifier(self, nonce: str) -> str:
        mac = hmac.new(self._secret.encode(), b"pkce:" + nonce.encode(), hashlib.sha256)
        return _b64(mac.digest())
