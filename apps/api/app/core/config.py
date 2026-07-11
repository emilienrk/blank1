from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"


class Settings(BaseSettings):
    """Configuration 12-factor : exclusivement via variables d'environnement.

    Les valeurs par défaut conviennent au dev local ; staging/prod les
    surchargent via l'environnement (jamais de secret dans le code).
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "staging", "prod"] = "dev"
    app_version: str = APP_VERSION
    log_level: str = "INFO"
    valkey_url: str = "redis://localhost:6379/0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
