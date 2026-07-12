"""Rate limiting par provider + backoff (Phase 5 T7) et verrous Valkey (D5).

Enveloppe COMMUNE de tout appel provider (invariant n°5 de la phase) :
- compteur fenêtre fixe Valkey par (provider, connexion), aligné sur les quotas
  publics (même brique que le rate limiting d'auth de la Phase 2, généralisée) ;
- respect de `Retry-After`, backoff exponentiel + jitter sur 429/5xx ;
- plafond de tentatives → erreur typée `ProviderUnavailable`.

Le même client Valkey porte les verrous par connexion (SET NX + TTL) qui
sérialisent le refresh périodique et le refresh à la volée (décision D5).
"""

# redis-py expose des types incomplets sur les commandes async.
# pyright: reportUnknownMemberType=false

import asyncio
import random
import uuid
from collections.abc import Awaitable, Callable

import redis.asyncio as aioredis
import structlog

from app.connectors.registry import ProviderManifest
from app.core.config import get_settings

logger = structlog.get_logger()

WINDOW_SECONDS = 60
MAX_ATTEMPTS = 5
LOCK_TTL_SECONDS = 30

_client: aioredis.Redis | None = None

# Frontière d'attente — remplacée par les tests (aucun vrai sleep en CI).
_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


def get_valkey_client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.Redis.from_url(get_settings().valkey_url)
    return _client


def set_valkey_client(client: aioredis.Redis | None) -> None:
    """Injection pour les tests (fakeredis) ou réinitialisation."""
    global _client
    _client = client


def set_sleep(sleep: Callable[[float], Awaitable[None]]) -> None:
    """Remplace la frontière d'attente (tests : aucun vrai sleep en CI)."""
    global _sleep
    _sleep = sleep


class ProviderUnavailable(RuntimeError):
    """Plafond de tentatives atteint : le provider est considéré indisponible."""


class ProviderResponseError(RuntimeError):
    """Réponse d'erreur provider, porteuse du statut HTTP et du Retry-After."""

    def __init__(
        self, status_code: int, retry_after: float | None = None, detail: str = ""
    ) -> None:
        super().__init__(f"HTTP {status_code} provider{f' — {detail}' if detail else ''}")
        self.status_code = status_code
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.status_code == 429 or self.status_code >= 500


async def _budget_delay(manifest: ProviderManifest, connection_id: uuid.UUID) -> float | None:
    """Compteur fenêtre fixe par (provider, connexion) : None si un slot est
    disponible, sinon le délai jusqu'à la prochaine fenêtre."""
    client = get_valkey_client()
    key = f"connector:rl:{manifest.provider.value}:{connection_id}"
    count = int(await client.incr(key))
    if count == 1:
        await client.expire(key, WINDOW_SECONDS)
    if count > manifest.requests_per_minute:
        ttl = int(await client.ttl(key))
        return float(max(ttl, 1))
    return None


def _backoff_delay(attempt_no: int) -> float:
    return (2.0**attempt_no) * 0.5 + random.uniform(0, 0.5)


async def run_with_backoff[T](
    manifest: ProviderManifest,
    connection_id: uuid.UUID,
    attempt: Callable[[], Awaitable[T]],
) -> T:
    """Exécute `attempt` sous budget local + backoff ; `ProviderUnavailable` au plafond."""
    last_error: ProviderResponseError | None = None
    for attempt_no in range(MAX_ATTEMPTS):
        budget_wait = await _budget_delay(manifest, connection_id)
        if budget_wait is not None:
            await _sleep(budget_wait)
        try:
            return await attempt()
        except ProviderResponseError as exc:
            if not exc.retryable:
                raise
            last_error = exc
            delay = exc.retry_after if exc.retry_after is not None else _backoff_delay(attempt_no)
            logger.info(
                "connector_provider_retry",
                provider=manifest.provider.value,
                connection_id=str(connection_id),
                status=exc.status_code,
                delay=round(delay, 2),
                attempt=attempt_no + 1,
            )
            await _sleep(delay)
    msg = f"Provider {manifest.provider.value} indisponible après {MAX_ATTEMPTS} tentatives."
    raise ProviderUnavailable(msg) from last_error


# --- Verrous par connexion (décision D5) ---


async def acquire_lock(name: str, ttl_seconds: int = LOCK_TTL_SECONDS) -> str | None:
    """SET NX + TTL ; retourne le jeton de libération, ou None si déjà pris."""
    token = uuid.uuid4().hex
    acquired = await get_valkey_client().set(
        f"connector:lock:{name}", token, nx=True, ex=ttl_seconds
    )
    return token if acquired else None


async def release_lock(name: str, token: str) -> None:
    """Libère le verrou seulement s'il nous appartient encore (TTL non expiré)."""
    client = get_valkey_client()
    key = f"connector:lock:{name}"
    current = await client.get(key)
    if current is not None and current.decode() == token:
        await client.delete(key)


async def wait_for_lock(name: str, *, attempts: int = 20, interval: float = 0.25) -> str:
    """Acquisition bloquante (refresh à la volée, D5) : réessaie jusqu'à obtention."""
    for _ in range(attempts):
        token = await acquire_lock(name)
        if token is not None:
            return token
        await _sleep(interval)
    msg = f"Verrou connecteur {name!r} toujours occupé après {attempts} tentatives."
    raise ProviderUnavailable(msg)
