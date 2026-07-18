import asyncio
import socket
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.core.crypto import reset_key_provider
from app.core.db import dispose_control_engine
from app.core.migrations import upgrade_database_sync
from app.main import create_app


def _postgres_available() -> bool:
    settings = Settings()
    try:
        with socket.create_connection((settings.postgres_host, settings.postgres_port), timeout=1):
            return True
    except OSError:
        return False


POSTGRES_AVAILABLE = _postgres_available()

# Les tests DB exigent un vrai Postgres (décision D6) : celui du Compose en local
# (`make infra`), le service postgres:17 en CI.
requires_postgres = pytest.mark.skipif(
    not POSTGRES_AVAILABLE, reason="Postgres requis (make infra) — décision D6 Phase 1"
)


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    """Chaque test repart d'une config propre (get_settings est mis en cache)."""
    get_settings.cache_clear()
    reset_key_provider()
    yield
    get_settings.cache_clear()
    reset_key_provider()


@pytest.fixture(autouse=True)
def fake_rate_limiter() -> Iterator[None]:
    """Valkey simulé pour le rate limiting : ni la CI ni les tests n'exigent Redis."""
    import fakeredis.aioredis

    from app.auth.rate_limit import set_rate_limit_client

    set_rate_limit_client(fakeredis.aioredis.FakeRedis())
    yield
    set_rate_limit_client(None)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


async def _admin_execute(admin_settings: Settings, sql: str) -> None:
    """Ordre administratif (CREATE/DROP DATABASE) sur le serveur, en autocommit."""
    engine = create_async_engine(
        admin_settings.database_url, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text(sql))
    finally:
        await engine.dispose()


@pytest.fixture
async def db_env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Settings]:
    """Base éphémère unique (ADR 0001) : créée, migrée à head, droppée en teardown."""
    # Settings d'admin capturées AVANT de patcher l'environnement.
    admin_settings = Settings()
    database = f"test_{uuid.uuid4().hex[:8]}"

    await _admin_execute(admin_settings, f'CREATE DATABASE "{database}"')
    monkeypatch.setenv("POSTGRES_DB", database)
    get_settings.cache_clear()
    await dispose_control_engine()

    settings = get_settings()
    await asyncio.to_thread(upgrade_database_sync, settings.database_url)
    try:
        yield settings
    finally:
        await dispose_control_engine()
        await _admin_execute(admin_settings, f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        get_settings.cache_clear()
