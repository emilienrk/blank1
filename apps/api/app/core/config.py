from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

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

    # --- PostgreSQL (control-plane + serveurs tenant) ---
    # Les URL ne sont JAMAIS stockées : toujours composées ici (décision D3 Phase 1).
    # Le catalogue ne contient que db_name + alias db_host ; credentials via env.
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "app"
    postgres_password: str = "change-me"
    postgres_db: str = "controlplane"

    # --- Multi-tenant ---
    tenant_db_prefix: str = "tenant_"
    tenant_engine_cache_size: int = 20
    tenant_engine_pool_size: int = 2

    def _database_url(self, database: str, host: str | None = None) -> str:
        url = URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=host or self.postgres_host,
            port=self.postgres_port,
            database=database,
        )
        return url.render_as_string(hide_password=False)

    @property
    def control_plane_url(self) -> str:
        return self._database_url(self.postgres_db)

    def resolve_db_host(self, db_host: str) -> str:
        """Alias logique du catalogue → hôte réel. `default` = serveur principal ;
        la répartition multi-serveurs (§8.7) branchera ici sa table de correspondance."""
        return self.postgres_host if db_host == "default" else db_host

    def tenant_database_url(self, db_name: str, db_host: str = "default") -> str:
        return self._database_url(db_name, host=self.resolve_db_host(db_host))


@lru_cache
def get_settings() -> Settings:
    return Settings()
