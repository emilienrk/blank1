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
from app.core.db import dispose_control_engine
from app.main import create_app
from app.tenancy.engine_manager import dispose_engine_manager
from app.tenancy.migrations_runner import upgrade_database_sync


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
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


async def _admin_execute(admin_settings: Settings, sql: str) -> None:
    """Ordre administratif (CREATE/DROP DATABASE) sur le serveur, en autocommit."""
    engine = create_async_engine(
        admin_settings.control_plane_url, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text(sql))
    finally:
        await engine.dispose()


async def _drop_databases_with_prefix(admin_settings: Settings, prefix: str) -> None:
    engine = create_async_engine(
        admin_settings.control_plane_url, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT datname FROM pg_database WHERE datname LIKE :pattern"),
                {"pattern": prefix + "%"},
            )
            names = [str(row[0]) for row in result]
            for name in names:
                await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        await engine.dispose()


@pytest.fixture
async def db_env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Settings]:
    """Environnement DB éphémère : control-plane dédié migré + préfixe tenant dédié.

    Tout est préfixé `test_` et droppé en teardown (décision D6).
    """
    # Settings d'admin capturées AVANT de patcher l'environnement.
    admin_settings = Settings()
    suffix = uuid.uuid4().hex[:8]
    controlplane_db = f"test_cp_{suffix}"
    tenant_prefix = f"test_tenant_{suffix}_"

    await _admin_execute(admin_settings, f'CREATE DATABASE "{controlplane_db}"')
    monkeypatch.setenv("POSTGRES_DB", controlplane_db)
    monkeypatch.setenv("TENANT_DB_PREFIX", tenant_prefix)
    get_settings.cache_clear()
    await dispose_control_engine()
    await dispose_engine_manager()

    settings = get_settings()
    await asyncio.to_thread(upgrade_database_sync, "controlplane", settings.control_plane_url)
    try:
        yield settings
    finally:
        await dispose_control_engine()
        await dispose_engine_manager()
        await _drop_databases_with_prefix(admin_settings, tenant_prefix)
        await _admin_execute(
            admin_settings, f'DROP DATABASE IF EXISTS "{controlplane_db}" WITH (FORCE)'
        )
        get_settings.cache_clear()
