import dataclasses
import os

import pytest

from services.token_vault import SealedToken, TokenDecryptError, TokenVault

K1, K2 = os.urandom(32), os.urandom(32)
CTX = "calendar:biz-1"


def test_round_trip_and_ciphertext_hides_the_plaintext():
    vault = TokenVault({"k1": K1}, "k1")
    sealed = vault.encrypt("1//refresh-token-value", context=CTX)

    assert sealed.key_id == "k1"
    assert b"refresh-token-value" not in sealed.ciphertext
    assert vault.decrypt(sealed, context=CTX) == "1//refresh-token-value"


def test_every_encryption_uses_a_fresh_nonce():
    vault = TokenVault({"k1": K1}, "k1")
    assert vault.encrypt("x", context=CTX) != vault.encrypt("x", context=CTX)


def test_a_ciphertext_does_not_open_under_another_context():
    vault = TokenVault({"k1": K1}, "k1")
    sealed = vault.encrypt("secret", context="calendar:biz-1")
    with pytest.raises(TokenDecryptError):
        vault.decrypt(sealed, context="calendar:biz-2")


def test_tampered_ciphertext_fails():
    vault = TokenVault({"k1": K1}, "k1")
    sealed = vault.encrypt("secret", context=CTX)
    flipped = bytearray(sealed.ciphertext)
    flipped[-1] ^= 1
    with pytest.raises(TokenDecryptError):
        vault.decrypt(dataclasses.replace(sealed, ciphertext=bytes(flipped)), context=CTX)
    with pytest.raises(TokenDecryptError):
        vault.decrypt(SealedToken("k1", b"short"), context=CTX)


def test_unknown_key_id_fails():
    sealed = TokenVault({"k1": K1}, "k1").encrypt("secret", context=CTX)
    with pytest.raises(TokenDecryptError):
        TokenVault({"k2": K2}, "k2").decrypt(sealed, context=CTX)


def test_relabelling_the_key_id_fails():
    sealed = TokenVault({"k1": K1}, "k1").encrypt("secret", context=CTX)
    both = TokenVault({"k1": K1, "k2": K1}, "k1")
    with pytest.raises(TokenDecryptError):
        both.decrypt(dataclasses.replace(sealed, key_id="k2"), context=CTX)


def test_rotation_old_key_still_decrypts_and_new_writes_use_the_active_key():
    old = TokenVault({"k1": K1}, "k1").encrypt("secret", context=CTX)
    rotated = TokenVault({"k2": K2, "k1": K1}, "k2")

    assert rotated.decrypt(old, context=CTX) == "secret"
    assert rotated.encrypt("secret", context=CTX).key_id == "k2"


def test_constructor_rejects_bad_keys():
    with pytest.raises(ValueError):
        TokenVault({"k1": K1}, "missing")
    with pytest.raises(ValueError):
        TokenVault({"k1": b"short"}, "k1")


def test_a_sealed_token_never_shows_its_ciphertext_in_its_repr():
    sealed = TokenVault({"k1": K1}, "k1").encrypt("secret", context=CTX)
    assert sealed.ciphertext.hex() not in repr(sealed)
    assert repr(sealed.ciphertext) not in repr(sealed)
    assert "k1" in repr(sealed)
