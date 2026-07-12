"""Cycle de vie des tokens de connecteurs (Phase 5 T3/T6).

Échange de code, refresh (périodique ET à la volée, sérialisés par verrou
Valkey — décision D5), révocation best-effort (décision D9). Les tokens ne
transitent qu'en mémoire : chiffrés KeyProvider avant toute écriture, jamais
loggés (invariant n°1 de la phase).

Écart D2 assumé (documenté au handoff) : l'échange/refresh OAuth passe par
`httpx` contre les endpoints du manifest pour LES DEUX providers — même
mécanique que l'OIDC manuel de la Phase 2, testable avec un faux provider
local. `msal` gèrerait un cache de tokens en mémoire qui entrerait en conflit
avec notre store chiffré en DB et le verrou par connexion.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit_event
from app.connectors import throttle
from app.connectors.registry import ProviderManifest
from app.connectors.tenant_models import ConnectionStatus, ConnectorConnection
from app.core.config import Settings, get_settings
from app.core.crypto import get_key_provider

logger = structlog.get_logger()

# Marge sous laquelle un access token est considéré expiré (refresh à la volée).
EXPIRY_SKEW_SECONDS = 60


class ConnectorError(RuntimeError):
    """Échec d'une opération connecteur (configuration, échange, refresh)."""


class InvalidGrantError(ConnectorError):
    """Refresh token invalidé côté provider (révocation) : re-consentement requis."""


class MissingRefreshTokenError(ConnectorError):
    """Le provider n'a pas émis de refresh token (consentement partiel Google)."""


@dataclass(frozen=True, slots=True)
class TokenBundle:
    access_token: str
    expires_in: int
    refresh_token: str | None = None
    scopes: list[str] = field(default_factory=lambda: cast(list[str], []))


def _http_client() -> httpx.AsyncClient:
    """Client HTTP sortant — les tests le remplacent (transport mocké)."""
    return httpx.AsyncClient(timeout=15)


_http_client_factory = _http_client


def http_client() -> httpx.AsyncClient:
    """Frontière HTTP sortante des connecteurs (client_base la partage)."""
    return _http_client_factory()


def encrypt_token(token: str) -> bytes:
    return get_key_provider().encrypt(token.encode())


def decrypt_token(sealed: bytes) -> str:
    return get_key_provider().decrypt(sealed).decode()


def client_credentials(manifest: ProviderManifest, settings: Settings) -> tuple[str, str]:
    """Credentials de l'app OAuth CONNECTEURS (décision D3 : distincte du login)."""
    if manifest.provider.value == "google":
        client_id = settings.google_connector_client_id
        client_secret = settings.google_connector_client_secret
    else:
        client_id = settings.microsoft_connector_client_id
        client_secret = settings.microsoft_connector_client_secret
    if not client_id or not client_secret:
        msg = f"App OAuth connecteurs {manifest.provider.value} non configurée."
        raise ConnectorError(msg)
    return client_id, client_secret


def _parse_token_response(response: httpx.Response, manifest: ProviderManifest) -> TokenBundle:
    if response.status_code != 200:
        error_code = ""
        try:
            raw: Any = response.json()
            if isinstance(raw, dict):
                error_code = str(cast(dict[str, Any], raw).get("error", ""))
        except ValueError:
            pass
        if error_code == "invalid_grant":
            msg = f"Refresh token invalidé par {manifest.provider.value} (invalid_grant)."
            raise InvalidGrantError(msg)
        # 429/5xx : récupérable (backoff appelant) ; autres 4xx : définitif.
        raise throttle.ProviderResponseError(response.status_code, detail="endpoint token")
    payload: dict[str, Any] = response.json()
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        msg = f"Réponse token de {manifest.provider.value} sans access_token."
        raise ConnectorError(msg)
    refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in", 3600)
    scope = payload.get("scope", "")
    return TokenBundle(
        access_token=access_token,
        expires_in=int(expires_in),
        refresh_token=refresh_token if isinstance(refresh_token, str) else None,
        scopes=scope.split() if isinstance(scope, str) else [],
    )


async def exchange_code(manifest: ProviderManifest, code: str, redirect_uri: str) -> TokenBundle:
    """Échange code d'autorisation → tokens (callback T3)."""
    client_id, client_secret = client_credentials(manifest, get_settings())
    async with _http_client_factory() as client:
        response = await client.post(
            manifest.token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
    return _parse_token_response(response, manifest)


async def refresh_token_grant(manifest: ProviderManifest, refresh_token: str) -> TokenBundle:
    """Rafraîchit un access token ; `InvalidGrantError` si le consentement est mort."""
    client_id, client_secret = client_credentials(manifest, get_settings())
    async with _http_client_factory() as client:
        response = await client.post(
            manifest.token_endpoint,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
    return _parse_token_response(response, manifest)


async def fetch_account_label(manifest: ProviderManifest, access_token: str) -> str:
    """Libellé du compte connecté (email) — affichage SPA uniquement."""
    async with _http_client_factory() as client:
        response = await client.get(
            manifest.account_info_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code != 200:
        return f"compte {manifest.provider.value}"
    payload: dict[str, Any] = response.json()
    return manifest.parse_account_label(payload) or f"compte {manifest.provider.value}"


def apply_token_bundle(connection: ConnectorConnection, bundle: TokenBundle) -> None:
    """Chiffre et pose les tokens sur la connexion ; santé OK."""
    connection.access_token_enc = encrypt_token(bundle.access_token)
    if bundle.refresh_token:
        # Microsoft fait tourner le refresh token à chaque échange ; Google ne le
        # renvoie qu'au consentement — on ne remplace que s'il y en a un nouveau.
        connection.refresh_token_enc = encrypt_token(bundle.refresh_token)
    connection.access_token_expires_at = datetime.now(UTC) + timedelta(seconds=bundle.expires_in)
    connection.status = ConnectionStatus.ACTIVE
    connection.last_error = None
    connection.health_checked_at = datetime.now(UTC)


def _refresh_lock_name(connection_id: uuid.UUID) -> str:
    return f"refresh:{connection_id}"


def access_token_expiring(connection: ConnectorConnection, lead: timedelta) -> bool:
    expires_at = connection.access_token_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC) + lead


async def refresh_connection(
    session: AsyncSession, connection: ConnectorConnection, *, block: bool = False
) -> bool:
    """Refresh sous verrou Valkey (décision D5) ; l'appelant commit.

    `block=False` (beat périodique) : s'abstient si le verrou est pris.
    `block=True` (refresh à la volée) : attend le verrou puis re-vérifie
    l'expiration — l'autre refresh a pu faire le travail.

    Retourne True si les tokens ont été renouvelés. `invalid_grant` bascule la
    connexion en `needs_reconsent` + audit (le re-consentement guidé du §5) sans
    lever. Les échecs récupérables (réseau, 5xx) lèvent : le beat réessaiera.
    """
    lock_name = _refresh_lock_name(connection.id)
    if block:
        token = await throttle.wait_for_lock(lock_name)
        # Le verrou obtenu, l'état a pu changer (refresh concurrent commité).
        await session.refresh(connection)
        if not access_token_expiring(connection, timedelta(seconds=EXPIRY_SKEW_SECONDS)):
            await throttle.release_lock(lock_name, token)
            return False
    else:
        maybe_token = await throttle.acquire_lock(lock_name)
        if maybe_token is None:
            logger.info("connector_refresh_skipped_locked", connection_id=str(connection.id))
            return False
        token = maybe_token

    manifest_provider = connection.provider
    from app.connectors.registry import get_provider

    manifest = get_provider(manifest_provider)
    try:
        refresh_token = decrypt_token(connection.refresh_token_enc)
        # Enveloppe throttle/backoff (invariant n°5) : 429/5xx du endpoint token
        # sont réessayés ; InvalidGrantError traverse (non récupérable).
        bundle = await throttle.run_with_backoff(
            manifest, connection.id, lambda: refresh_token_grant(manifest, refresh_token)
        )
    except InvalidGrantError:
        connection.status = ConnectionStatus.NEEDS_RECONSENT
        connection.last_error = "Refresh token invalidé par le provider (invalid_grant)."
        connection.health_checked_at = datetime.now(UTC)
        await record_audit_event(
            session,
            action="connector.reconsent_required",
            resource_type="connector_connection",
            resource_id=str(connection.id),
            payload={"provider": connection.provider.value, "account": connection.account_label},
        )
        await session.flush()
        logger.warning(
            "connector_reconsent_required",
            connection_id=str(connection.id),
            provider=connection.provider.value,
        )
        return False
    except (throttle.ProviderResponseError, throttle.ProviderUnavailable, httpx.HTTPError) as exc:
        connection.last_error = f"Refresh échoué : {exc.__class__.__name__}"
        connection.health_checked_at = datetime.now(UTC)
        await session.flush()
        raise
    finally:
        await throttle.release_lock(lock_name, token)

    apply_token_bundle(connection, bundle)
    await session.flush()
    logger.info(
        "connector_tokens_refreshed",
        connection_id=str(connection.id),
        provider=connection.provider.value,
    )
    return True


async def revoke_connection(session: AsyncSession, connection: ConnectorConnection) -> None:
    """Révocation : best-effort côté provider, JAMAIS bloquante (décision D9) —
    la suppression locale des tokens protège quoi qu'il arrive."""
    from app.connectors.registry import get_provider

    manifest = get_provider(connection.provider)
    if manifest.revoke_endpoint is not None:
        try:
            refresh_token = decrypt_token(connection.refresh_token_enc)
            async with _http_client_factory() as client:
                await client.post(manifest.revoke_endpoint, data={"token": refresh_token})
        except Exception as exc:
            logger.warning(
                "connector_remote_revoke_failed",
                connection_id=str(connection.id),
                provider=connection.provider.value,
                error=exc.__class__.__name__,
            )
    connection.access_token_enc = b""
    connection.refresh_token_enc = b""
    connection.status = ConnectionStatus.REVOKED
    connection.last_error = None
    connection.health_checked_at = datetime.now(UTC)
    await session.flush()
