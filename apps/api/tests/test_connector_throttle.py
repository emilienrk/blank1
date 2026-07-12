"""Rate limiting + backoff des connecteurs (Phase 5 T7).

429 avec Retry-After respecté ; backoff croissant ; plafond → ProviderUnavailable ;
compteurs par connexion indépendants ; verrous SET NX exclusifs.
"""

import uuid
from collections.abc import Iterator

import pytest

from app.connectors import throttle
from tests.connector_helpers import (
    fake_google_manifest,
    install_fake_valkey,
    reset_connector_throttle,
)


@pytest.fixture(autouse=True)
def connector_valkey(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    install_fake_valkey(monkeypatch)
    yield
    reset_connector_throttle()


async def test_retry_after_respected_then_success() -> None:
    manifest = fake_google_manifest()
    connection_id = uuid.uuid4()
    delays: list[float] = []
    throttle.set_sleep(lambda d: _record(delays, d))

    attempts = 0

    async def attempt() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise throttle.ProviderResponseError(429, retry_after=7.0)
        return "ok"

    result = await throttle.run_with_backoff(manifest, connection_id, attempt)
    assert result == "ok"
    assert attempts == 2
    # Le Retry-After du provider prime sur le backoff calculé.
    assert 7.0 in delays


async def test_backoff_grows_across_retries() -> None:
    manifest = fake_google_manifest()
    connection_id = uuid.uuid4()
    delays: list[float] = []
    throttle.set_sleep(lambda d: _record(delays, d))

    attempts = 0

    async def attempt() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise throttle.ProviderResponseError(503)  # 5xx, sans Retry-After
        return "ok"

    result = await throttle.run_with_backoff(manifest, connection_id, attempt)
    assert result == "ok"
    # Deux backoffs calculés, croissants (2^0*0.5+j puis 2^1*0.5+j).
    assert len(delays) == 2
    assert delays[1] > delays[0]


async def test_cap_reached_raises_provider_unavailable() -> None:
    manifest = fake_google_manifest()
    throttle.set_sleep(lambda _: _noop())

    async def always_429() -> str:
        raise throttle.ProviderResponseError(429)

    with pytest.raises(throttle.ProviderUnavailable):
        await throttle.run_with_backoff(manifest, uuid.uuid4(), always_429)


async def test_non_retryable_error_propagates() -> None:
    manifest = fake_google_manifest()
    throttle.set_sleep(lambda _: _noop())

    async def forbidden() -> str:
        raise throttle.ProviderResponseError(403)  # 4xx hors 429 : définitif

    with pytest.raises(throttle.ProviderResponseError):
        await throttle.run_with_backoff(manifest, uuid.uuid4(), forbidden)


async def test_budget_counters_are_per_connection() -> None:
    # Un manifest à 1 req/min : la 2e requête sur la MÊME connexion attend, mais
    # une autre connexion a son propre compteur (aucune attente).
    manifest = fake_google_manifest()
    object.__setattr__(manifest, "requests_per_minute", 1)
    waits: list[float] = []
    throttle.set_sleep(lambda d: _record(waits, d))

    conn_a, conn_b = uuid.uuid4(), uuid.uuid4()

    async def ok() -> str:
        return "ok"

    await throttle.run_with_backoff(manifest, conn_a, ok)  # slot 1 de A
    await throttle.run_with_backoff(manifest, conn_a, ok)  # dépasse le budget de A → attente
    assert len(waits) == 1
    await throttle.run_with_backoff(manifest, conn_b, ok)  # compteur indépendant → pas d'attente
    assert len(waits) == 1


async def test_lock_is_exclusive() -> None:
    name = f"test-{uuid.uuid4()}"
    token = await throttle.acquire_lock(name)
    assert token is not None
    # Verrou déjà pris : seconde acquisition refusée.
    assert await throttle.acquire_lock(name) is None
    # Libéré, il redevient disponible.
    await throttle.release_lock(name, token)
    assert await throttle.acquire_lock(name) is not None


async def _record(bucket: list[float], delay: float) -> None:
    bucket.append(delay)


async def _noop() -> None:
    return None
