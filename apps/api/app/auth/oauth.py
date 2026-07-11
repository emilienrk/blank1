"""OAuth login Google & Microsoft (plan global §4, Phase 2 T6).

Login social UNIQUEMENT — les connecteurs OAuth tiers (tokens d'API) arrivent
en Phase 5. Flux code OIDC avec `state` signé auto-porteur (il transporte le
sous-domaine de retour et le nonce ; pas d'état serveur — c'est pourquoi on
n'utilise pas l'intégration Starlette d'Authlib, qui exige une session cookie).
Authlib fournit la partie JOSE : validation de l'id_token contre le JWKS du
provider. Décision D5 : l'utilisateur DOIT déjà exister (invitation only),
la liaison se fait par email vérifié au premier login puis par (provider, subject).
"""

# Authlib n'expose pas de types.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from authlib.jose import JsonWebToken
from authlib.jose.errors import JoseError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import OAuthIdentity, OAuthProvider
from app.auth.service import get_user_by_email
from app.auth.tokens import generate_token, sign_payload, verify_payload
from app.core.config import Settings, get_settings
from app.directory.models import User

logger = structlog.get_logger()

STATE_TTL_SECONDS = 600

GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"
MICROSOFT_METADATA_URL = (
    "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration"
)

# Surchargeable par les tests (faux provider OIDC local).
_metadata_urls: dict[OAuthProvider, str] = {
    OAuthProvider.GOOGLE: GOOGLE_METADATA_URL,
    OAuthProvider.MICROSOFT: MICROSOFT_METADATA_URL,
}


class OAuthLoginError(RuntimeError):
    """Échec du flux OAuth login (state, échange de code, id_token, user inconnu)."""


@dataclass(frozen=True, slots=True)
class OAuthUserInfo:
    provider: OAuthProvider
    subject: str
    email: str
    email_verified: bool


def _client_credentials(provider: OAuthProvider, settings: Settings) -> tuple[str, str]:
    if provider is OAuthProvider.GOOGLE:
        client_id, secret = settings.google_client_id, settings.google_client_secret
    else:
        client_id, secret = settings.microsoft_client_id, settings.microsoft_client_secret
    if not client_id or not secret:
        msg = f"Provider OAuth {provider} non configuré (client_id/client_secret manquants)."
        raise OAuthLoginError(msg)
    return client_id, secret


def redirect_uri(provider: OAuthProvider, settings: Settings) -> str:
    return f"{settings.public_base_url}/api/v1/auth/oauth/{provider.value}/callback"


def _http_client() -> httpx.AsyncClient:
    """Client HTTP sortant — les tests le remplacent (transport mocké)."""
    return httpx.AsyncClient(timeout=10)


_http_client_factory = _http_client


async def _fetch_metadata(provider: OAuthProvider) -> dict[str, Any]:
    async with _http_client_factory() as client:
        response = await client.get(_metadata_urls[provider])
        response.raise_for_status()
        metadata: dict[str, Any] = response.json()
        return metadata


async def build_authorization_url(provider: OAuthProvider, return_host: str) -> str:
    """Construit l'URL d'autorisation ; le state signé porte retour + nonce."""
    settings = get_settings()
    client_id, _ = _client_credentials(provider, settings)
    metadata = await _fetch_metadata(provider)
    nonce = generate_token()
    state = sign_payload(
        {"p": provider.value, "r": return_host, "n": nonce}, ttl_seconds=STATE_TTL_SECONDS
    )
    params = httpx.QueryParams(
        response_type="code",
        client_id=client_id,
        redirect_uri=redirect_uri(provider, settings),
        scope="openid email profile",
        state=state,
        nonce=nonce,
    )
    authorization_endpoint: str = metadata["authorization_endpoint"]
    return f"{authorization_endpoint}?{params}"


async def _exchange_code(
    provider: OAuthProvider, code: str, metadata: dict[str, Any], settings: Settings
) -> str:
    """Échange le code contre les tokens ; retourne l'id_token brut."""
    client_id, client_secret = _client_credentials(provider, settings)
    async with _http_client_factory() as client:
        response = await client.post(
            metadata["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri(provider, settings),
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
    if response.status_code != 200:
        msg = f"Échange de code refusé par {provider} (HTTP {response.status_code})."
        raise OAuthLoginError(msg)
    id_token = response.json().get("id_token")
    if not isinstance(id_token, str):
        msg = f"Réponse token de {provider} sans id_token."
        raise OAuthLoginError(msg)
    return id_token


async def _validate_id_token(
    provider: OAuthProvider, id_token: str, nonce: str, metadata: dict[str, Any], settings: Settings
) -> OAuthUserInfo:
    """Valide signature (JWKS), audience, expiration et nonce de l'id_token."""
    client_id, _ = _client_credentials(provider, settings)
    async with _http_client_factory() as client:
        response = await client.get(metadata["jwks_uri"])
        response.raise_for_status()
        jwks = response.json()

    jwt = JsonWebToken(["RS256"])
    try:
        claims = jwt.decode(
            id_token,
            jwks,
            claims_options={
                "aud": {"essential": True, "value": client_id},
                "exp": {"essential": True},
            },
        )
        claims.validate(now=int(time.time()))
    except JoseError as exc:
        msg = f"id_token invalide ({provider})."
        raise OAuthLoginError(msg) from exc

    if claims.get("nonce") != nonce:
        msg = "Nonce de l'id_token inattendu."
        raise OAuthLoginError(msg)
    email = claims.get("email")
    subject = claims.get("sub")
    if not isinstance(email, str) or not isinstance(subject, str):
        msg = "id_token sans email ou subject."
        raise OAuthLoginError(msg)
    return OAuthUserInfo(
        provider=provider,
        subject=subject,
        email=email,
        email_verified=bool(claims.get("email_verified", False)),
    )


def parse_state(state: str, expected_provider: OAuthProvider) -> tuple[str, str]:
    """Vérifie le state signé ; retourne (host de retour, nonce)."""
    try:
        payload = verify_payload(state)
    except ValueError as exc:
        msg = "State OAuth invalide ou expiré."
        raise OAuthLoginError(msg) from exc
    if payload.get("p") != expected_provider.value:
        msg = "State OAuth émis pour un autre provider."
        raise OAuthLoginError(msg)
    return_host, nonce = payload.get("r"), payload.get("n")
    if not isinstance(return_host, str) or not isinstance(nonce, str):
        msg = "State OAuth incomplet."
        raise OAuthLoginError(msg)
    return return_host, nonce


async def complete_login(
    session: AsyncSession, provider: OAuthProvider, code: str, nonce: str
) -> User:
    """Callback : code → id_token validé → user existant (D5 : jamais de création)."""
    settings = get_settings()
    metadata = await _fetch_metadata(provider)
    id_token = await _exchange_code(provider, code, metadata, settings)
    info = await _validate_id_token(provider, id_token, nonce, metadata, settings)

    identity = await session.scalar(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == provider, OAuthIdentity.subject == info.subject
        )
    )
    if identity is not None:
        user = await session.get(User, identity.user_id)
        if user is None:  # pragma: no cover — FK garantit l'intégrité
            msg = "Identité OAuth orpheline."
            raise OAuthLoginError(msg)
        return user

    # Premier login : liaison par email vérifié à un compte INVITÉ existant.
    if not info.email_verified:
        msg = f"Email non vérifié chez {provider} — liaison refusée."
        raise OAuthLoginError(msg)
    user = await get_user_by_email(session, info.email)
    if user is None:
        msg = "Aucun compte pour cette identité — l'inscription se fait sur invitation."
        raise OAuthLoginError(msg)
    session.add(OAuthIdentity(provider=provider, subject=info.subject, user_id=user.id))
    logger.info("oauth_identity_linked", user_id=str(user.id), provider=provider.value)
    return user
