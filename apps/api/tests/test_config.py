import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("VALKEY_URL", "redis://elsewhere:6379/1")

    settings = Settings()

    assert settings.app_env == "staging"
    assert settings.log_level == "DEBUG"
    assert settings.valkey_url == "redis://elsewhere:6379/1"


def test_settings_reject_invalid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production-typo")

    with pytest.raises(ValidationError):
        Settings()


def test_database_urls_are_composed_never_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_USER", "svc")
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    monkeypatch.setenv("POSTGRES_DB", "cp")

    settings = Settings()

    assert settings.control_plane_url == "postgresql+asyncpg://svc:s3cret@db.internal:5433/cp"
    # Alias `default` → serveur principal ; alias explicite → hôte dédié (§8.7).
    assert (
        settings.tenant_database_url("tenant_acme")
        == "postgresql+asyncpg://svc:s3cret@db.internal:5433/tenant_acme"
    )
    assert (
        settings.tenant_database_url("tenant_acme", db_host="pg2.internal")
        == "postgresql+asyncpg://svc:s3cret@pg2.internal:5433/tenant_acme"
    )
