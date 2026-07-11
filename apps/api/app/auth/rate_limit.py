"""Rate limiting des endpoints d'auth (décision D9 Phase 2).

Compteurs fenêtre fixe sur Valkey (déjà dans la stack), par IP ET par cible
(email/token) : login, login/totp, acceptation d'invitation → 429 au-delà du
seuil. Le rate limiting global reste en Phase 8 comme prévu (§9).
"""

# redis-py expose des types incomplets sur les commandes async.
# pyright: reportUnknownMemberType=false

import hashlib

import redis.asyncio as aioredis
from fastapi import HTTPException, Request

from app.core.config import get_settings

_client: aioredis.Redis | None = None


def get_rate_limit_client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.Redis.from_url(get_settings().valkey_url)
    return _client


def set_rate_limit_client(client: aioredis.Redis | None) -> None:
    """Injection pour les tests (fakeredis) ou réinitialisation."""
    global _client
    _client = client


def _key(scope: str, identifier: str) -> str:
    # L'identifiant (email, IP) est haché : jamais de PII dans Valkey non plus.
    digest = hashlib.sha256(identifier.lower().encode()).hexdigest()[:32]
    return f"rl:{scope}:{digest}"


async def enforce_rate_limit(request: Request, scope: str, *identifiers: str) -> None:
    """Incrémente les compteurs (IP + identifiants métier) ; 429 au-delà du seuil."""
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    keys = [_key(scope, client_ip)] + [_key(scope, ident) for ident in identifiers if ident]

    client = get_rate_limit_client()
    for key in keys:
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, settings.auth_rate_limit_window_seconds)
        if count > settings.auth_rate_limit_attempts:
            raise HTTPException(status_code=429, detail="Trop de tentatives — réessayez plus tard.")
