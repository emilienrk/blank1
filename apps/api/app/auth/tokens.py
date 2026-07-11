"""Tokens opaques et données signées — mécanique commune de l'auth.

Invariant Phase 2 n°2 : un token en clair n'existe que dans la réponse à son
créateur ; seul son hash sha256 est stocké. Les payloads signés (state OAuth)
utilisent HMAC-SHA256 avec la clé maître.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from app.core.config import get_settings

TOKEN_BYTES = 32  # 256 bits


class InvalidSignedPayloadError(ValueError):
    """Payload signé illisible, falsifié ou expiré."""


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _signature(payload: str) -> str:
    key = get_settings().master_key_bytes()
    return _b64encode(hmac.new(key, payload.encode(), hashlib.sha256).digest())


def sign_payload(data: dict[str, Any], ttl_seconds: int) -> str:
    """Sérialise et signe un payload avec expiration (state OAuth, décision T6)."""
    body = dict(data)
    body["exp"] = int(time.time()) + ttl_seconds
    payload = _b64encode(json.dumps(body, separators=(",", ":")).encode())
    return f"{payload}.{_signature(payload)}"


def verify_payload(signed: str) -> dict[str, Any]:
    """Vérifie signature et expiration ; retourne le payload (sans `exp`)."""
    payload, _, signature = signed.partition(".")
    if not payload or not signature:
        msg = "Payload signé malformé."
        raise InvalidSignedPayloadError(msg)
    if not hmac.compare_digest(signature, _signature(payload)):
        msg = "Signature invalide."
        raise InvalidSignedPayloadError(msg)
    try:
        body: dict[str, Any] = json.loads(_b64decode(payload))
    except (ValueError, UnicodeDecodeError) as exc:
        msg = "Payload signé illisible."
        raise InvalidSignedPayloadError(msg) from exc
    expires_at = body.pop("exp", None)
    if not isinstance(expires_at, int) or expires_at < time.time():
        msg = "Payload signé expiré."
        raise InvalidSignedPayloadError(msg)
    return body
