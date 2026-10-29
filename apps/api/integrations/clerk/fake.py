"""In-memory fakes of the Clerk ports, for tests.

`build_fake_signing_key_resolver` returns one fixed PEM key for every token instead of
fetching a JWKS, so `core.auth.verify_principal` can be exercised against real RS256-signed
tokens (expired, wrong issuer, tampered, ...) without any network call.

`FakeClerkUsersApi` records every delete and can be scripted to fail.
"""

from integrations.clerk.port import ClerkApiError, SigningKeyResolver


def build_fake_signing_key_resolver(public_key_pem: str) -> SigningKeyResolver:
    async def resolve(token: str) -> str:
        return public_key_pem

    return resolve


class FakeClerkUsersApi:
    def __init__(self) -> None:
        # Every `delete_user` call, in order, including ones that failed.
        self.calls: list[str] = []
        # Users Clerk actually removed.
        self.deleted: list[str] = []
        self._failures: list[ClerkApiError] = []

    def fail_next(self, error: ClerkApiError, *, times: int = 1) -> None:
        self._failures.extend([error] * times)

    async def delete_user(self, user_id: str) -> None:
        self.calls.append(user_id)
        if self._failures:
            raise self._failures.pop(0)
        if user_id not in self.deleted:  # a repeat finds nothing, which is success
            self.deleted.append(user_id)
