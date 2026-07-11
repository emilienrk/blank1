"""Chiffrement applicatif en enveloppe — interface `KeyProvider` (plan global §1).

Introduit en Phase 2 (décision D4) pour les secrets TOTP ; les tokens des
connecteurs (Phase 5) réutiliseront cette interface. L'implémentation de départ
chiffre en AES-256-GCM avec la clé maître d'environnement ; clés par tenant,
OpenBao ou KMS se brancheront derrière la même interface sans toucher aux appelants.
"""

import os
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

_NONCE_SIZE = 12


class CryptoError(RuntimeError):
    """Échec de chiffrement/déchiffrement (clé invalide, données corrompues)."""


class KeyProvider(Protocol):
    def encrypt(self, plaintext: bytes) -> bytes: ...

    def decrypt(self, ciphertext: bytes) -> bytes: ...


class EnvKeyProvider:
    """AES-256-GCM avec la clé maître d'environnement ; nonce aléatoire préfixé."""

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != 32:
            msg = "La clé maître doit faire exactement 32 octets (AES-256)."
            raise CryptoError(msg)
        self._aesgcm = AESGCM(master_key)

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = os.urandom(_NONCE_SIZE)
        return nonce + self._aesgcm.encrypt(nonce, plaintext, None)

    def decrypt(self, ciphertext: bytes) -> bytes:
        if len(ciphertext) <= _NONCE_SIZE:
            msg = "Données chiffrées tronquées."
            raise CryptoError(msg)
        nonce, sealed = ciphertext[:_NONCE_SIZE], ciphertext[_NONCE_SIZE:]
        try:
            return self._aesgcm.decrypt(nonce, sealed, None)
        except InvalidTag as exc:
            msg = "Déchiffrement impossible : clé invalide ou données corrompues."
            raise CryptoError(msg) from exc


_provider: KeyProvider | None = None


def get_key_provider() -> KeyProvider:
    global _provider
    if _provider is None:
        _provider = EnvKeyProvider(get_settings().master_key_bytes())
    return _provider


def reset_key_provider() -> None:
    """Oublie le provider mis en cache (tests, changement de config)."""
    global _provider
    _provider = None
