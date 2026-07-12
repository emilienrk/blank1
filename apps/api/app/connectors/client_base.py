"""Mécanique commune des implémentations de capabilities (Phase 5 T4).

- `fresh_access_token` : déchiffre l'access token, le rafraîchit à la volée s'il
  expire (sous le MÊME verrou que le refresh périodique — décision D5) ;
- `provider_http_request` : tout appel REST provider (Graph) sous l'enveloppe
  throttle/backoff (invariant n°5) ;
- `run_sync_call` : exécution des SDK synchrones (google-api-python-client)
  hors event loop (`anyio.to_thread`, décision D4).
"""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import anyio.to_thread
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import service, throttle
from app.connectors.registry import ProviderManifest, get_provider
from app.connectors.tenant_models import ConnectionStatus, ConnectorConnection


class ConnectorCallError(RuntimeError):
    """Erreur définitive d'un appel provider (4xx hors 429) — résumé technique
    uniquement, jamais le corps de la réponse (il peut contenir du métier)."""


async def fresh_access_token(session: AsyncSession, connection: ConnectorConnection) -> str:
    """Access token valide, rafraîchi à la volée si nécessaire (verrou D5)."""
    if connection.status is not ConnectionStatus.ACTIVE:
        msg = f"Connexion {connection.id} non active ({connection.status.value})."
        raise ConnectorCallError(msg)
    if service.access_token_expiring(connection, timedelta(seconds=service.EXPIRY_SKEW_SECONDS)):
        await service.refresh_connection(session, connection, block=True)
        await session.commit()
    return service.decrypt_token(connection.access_token_enc)


async def run_sync_call[T](func: Callable[[], T]) -> T:
    """Exécute un appel SDK synchrone dans le threadpool (jamais dans l'event loop)."""
    return await anyio.to_thread.run_sync(func)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


async def provider_http_request(
    manifest: ProviderManifest,
    connection: ConnectorConnection,
    method: str,
    url: str,
    *,
    access_token: str,
    json: Any | None = None,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    """Appel REST provider sous l'enveloppe throttle/backoff (invariant n°5)."""

    async def attempt() -> httpx.Response:
        async with service.http_client() as client:
            response = await client.request(
                method,
                url,
                json=json,
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise throttle.ProviderResponseError(
                response.status_code, retry_after=_retry_after_seconds(response)
            )
        return response

    response = await throttle.run_with_backoff(manifest, connection.id, attempt)
    if response.status_code >= 400:
        msg = (
            f"Appel {manifest.provider.value} refusé (HTTP {response.status_code}, "
            f"{method} {httpx.URL(url).path})."
        )
        raise ConnectorCallError(msg)
    return response


async def graph_request(
    session: AsyncSession,
    connection: ConnectorConnection,
    method: str,
    path: str,
    *,
    json: Any | None = None,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    """Raccourci Microsoft Graph : token frais + URL absolue depuis le manifest."""
    manifest = get_provider(connection.provider)
    access_token = await fresh_access_token(session, connection)
    return await provider_http_request(
        manifest,
        connection,
        method,
        f"{manifest.api_base_url}{path}",
        access_token=access_token,
        json=json,
        params=params,
    )
