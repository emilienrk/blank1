import base64

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


def test_master_key_dev_default_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    # Dev sans clé : clé dérivée fixe (32 octets), jamais utilisée hors dev.
    assert len(Settings().master_key_bytes()) == 32

    # Hors dev, la clé est obligatoire (invariant : pas de démarrage sans clé).
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(ValueError, match="obligatoire"):
        Settings().master_key_bytes()

    # Clé explicite : base64 de 32 octets exigé.
    monkeypatch.setenv("AUTH_MASTER_KEY", base64.b64encode(b"a" * 32).decode())
    assert Settings().master_key_bytes() == b"a" * 32
    monkeypatch.setenv("AUTH_MASTER_KEY", base64.b64encode(b"court").decode())
    with pytest.raises(ValueError, match="32 octets"):
        Settings().master_key_bytes()


def test_session_cookie_secure_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings().session_cookie_secure is False
    monkeypatch.setenv("APP_ENV", "staging")
    assert Settings().session_cookie_secure is True


def test_database_url_is_composed_never_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_USER", "svc")
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    monkeypatch.setenv("POSTGRES_DB", "saas")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://svc:s3cret@db.internal:5433/saas"
