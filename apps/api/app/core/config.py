import base64
import hashlib
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

    # --- Auth (Phase 2) ---
    # Clé maître AES-256 encodée base64 (32 octets décodés). Obligatoire hors dev ;
    # en dev une clé dérivée fixe est utilisée si absente (jamais en staging/prod).
    auth_master_key: str = ""
    session_ttl_hours: int = 168
    session_cookie_name: str = "saas_session"
    # Domaine du cookie de session (décision D2) : `.staging.<domaine>` en staging
    # pour partager la session entre sous-domaines tenant ; vide = host-only (dev).
    session_cookie_domain: str = ""
    # URL publique apex de la plateforme (callbacks OAuth, liens d'invitation).
    public_base_url: str = "http://localhost:8000"
    invitation_ttl_hours: int = 168
    login_challenge_ttl_minutes: int = 5

    # --- OAuth login (Authlib, décision D5 : invitation only) ---
    google_client_id: str = ""
    google_client_secret: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""

    # --- Emails transactionnels (décision D8 : SMTP optionnel) ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_sender: str = ""

    # --- Rate limiting des endpoints d'auth (décision D9) ---
    auth_rate_limit_attempts: int = 10
    auth_rate_limit_window_seconds: int = 300

    # --- Audit + RGPD (Phase 4) ---
    audit_retention_days: int = 365
    gdpr_export_dir: str = "/var/lib/saas/gdpr-exports"
    gdpr_export_ttl_days: int = 7
    gdpr_erasure_grace_days: int = 7

    def master_key_bytes(self) -> bytes:
        """Clé maître de chiffrement (32 octets). Refuse de démarrer sans clé hors dev."""
        if self.auth_master_key:
            key = base64.b64decode(self.auth_master_key)
            if len(key) != 32:
                msg = "AUTH_MASTER_KEY doit décoder exactement 32 octets (AES-256)."
                raise ValueError(msg)
            return key
        if self.app_env == "dev":
            return hashlib.sha256(b"saas-dev-master-key-not-a-secret").digest()
        msg = "AUTH_MASTER_KEY est obligatoire hors environnement dev."
        raise ValueError(msg)

    @property
    def session_cookie_secure(self) -> bool:
        return self.app_env != "dev"

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
