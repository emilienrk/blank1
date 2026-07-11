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
