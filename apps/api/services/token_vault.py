"""`TokenVault`: authenticated encryption for secrets stored in the database.

AES-256-GCM. A sealed value is `key_id` plus `nonce (12 random bytes) || ciphertext+tag`.
The additional authenticated data is `tether:v1:<key_id>:<context>`, and callers pass
the owning row as the context (`calendar:<business_id>`), so a ciphertext copied into
another business's row, or re-labelled with another key id, fails to decrypt.

Rotation: `keys` may hold several; `active_key_id` seals, every key decrypts. A row
sealed with an older key is re-sealed with the active one the next time the connection
is probed successfully (`services/calendar.CalendarConnections.status`). Pure
crypto, no settings, no logging: errors carry no detail on purpose.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12
_KEY_BYTES = 32


class TokenDecryptError(Exception):
    """The value can't be opened: unknown key id, wrong context, or tampered bytes."""


@dataclass(frozen=True)
class SealedToken:
    key_id: str
    ciphertext: bytes = field(repr=False)


class TokenVault:
    def __init__(self, keys: Mapping[str, bytes], active_key_id: str) -> None:
        if active_key_id not in keys:
            raise ValueError("The active key id must be one of the keys.")
        if any(len(key) != _KEY_BYTES for key in keys.values()):
            raise ValueError("Every key must be 32 bytes.")
        self._ciphers = {key_id: AESGCM(key) for key_id, key in keys.items()}
        self._active = active_key_id

    @property
    def active_key_id(self) -> str:
        return self._active

    def encrypt(self, plaintext: str, *, context: str) -> SealedToken:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._ciphers[self._active].encrypt(
            nonce, plaintext.encode(), _aad(self._active, context)
        )
        return SealedToken(key_id=self._active, ciphertext=nonce + sealed)

    def decrypt(self, sealed: SealedToken, *, context: str) -> str:
        cipher = self._ciphers.get(sealed.key_id)
        if cipher is None or len(sealed.ciphertext) <= _NONCE_BYTES:
            raise TokenDecryptError
        nonce, body = sealed.ciphertext[:_NONCE_BYTES], sealed.ciphertext[_NONCE_BYTES:]
        try:
            return cipher.decrypt(nonce, body, _aad(sealed.key_id, context)).decode()
        except (InvalidTag, UnicodeDecodeError):
            raise TokenDecryptError from None


def _aad(key_id: str, context: str) -> bytes:
    return f"tether:v1:{key_id}:{context}".encode()
