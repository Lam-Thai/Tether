"""The port: how `core.auth` asks for the public key that signed a token.

Narrow on purpose — a callable from a raw JWT to its PEM-encoded signing
key — so `core.auth.verify_principal` never has to know whether that key
came from a live JWKS fetch or a test fixture.
"""

from collections.abc import Awaitable, Callable

SigningKeyResolver = Callable[[str], Awaitable[str]]
