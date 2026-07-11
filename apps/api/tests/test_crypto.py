"""KeyProvider AES-256-GCM (Phase 2 T2, décision D4)."""

import os

import pytest

from app.core.crypto import CryptoError, EnvKeyProvider, get_key_provider


def test_encrypt_decrypt_roundtrip() -> None:
    provider = EnvKeyProvider(os.urandom(32))
    plaintext = b"secret TOTP base32"
    assert provider.decrypt(provider.encrypt(plaintext)) == plaintext


def test_same_plaintext_yields_different_ciphertexts() -> None:
    provider = EnvKeyProvider(os.urandom(32))
    assert provider.encrypt(b"x") != provider.encrypt(b"x")  # nonce aléatoire


def test_wrong_key_fails_explicitly() -> None:
    sealed = EnvKeyProvider(os.urandom(32)).encrypt(b"data")
    other = EnvKeyProvider(os.urandom(32))
    with pytest.raises(CryptoError):
        other.decrypt(sealed)


def test_corrupted_ciphertext_fails() -> None:
    provider = EnvKeyProvider(os.urandom(32))
    sealed = bytearray(provider.encrypt(b"data"))
    sealed[-1] ^= 0xFF
    with pytest.raises(CryptoError):
        provider.decrypt(bytes(sealed))


def test_truncated_ciphertext_fails() -> None:
    provider = EnvKeyProvider(os.urandom(32))
    with pytest.raises(CryptoError):
        provider.decrypt(b"court")


def test_invalid_key_length_rejected() -> None:
    with pytest.raises(CryptoError):
        EnvKeyProvider(b"trop-courte")


def test_get_key_provider_uses_dev_default_key() -> None:
    # En dev sans AUTH_MASTER_KEY, une clé dérivée fixe permet de démarrer.
    provider = get_key_provider()
    assert provider.decrypt(provider.encrypt(b"ok")) == b"ok"
