"""Manifest Microsoft 365 (Phase 5 T2) : Mail + Calendar via Microsoft Graph.

Décision D2 : les appels Graph passent par `httpx` (REST direct), pas de
`msgraph-sdk`. `offline_access` est indispensable pour obtenir un refresh token.

Spécificité webhooks : subscriptions Graph (~3 jours max pour les messages),
renouvelables par PATCH ; la validation d'endpoint se fait par echo du
`validationToken` et chaque notification rejoue le `clientState`.
"""

from typing import Any

from app.connectors.registry import (
    CAPABILITY_CALENDAR,
    CAPABILITY_MAIL,
    ProviderManifest,
)
from app.connectors.tenant_models import ConnectorProvider


def _account_label(payload: dict[str, Any]) -> str | None:
    for key in ("mail", "userPrincipalName"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


MICROSOFT_MANIFEST = ProviderManifest(
    provider=ConnectorProvider.MICROSOFT,
    authorization_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    token_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/token",
    account_info_endpoint="https://graph.microsoft.com/v1.0/me",
    parse_account_label=_account_label,
    base_scopes=("openid", "email", "offline_access"),
    capability_scopes={
        CAPABILITY_MAIL: ("Mail.Read", "Mail.Send"),
        CAPABILITY_CALENDAR: ("Calendars.ReadWrite",),
    },
    api_base_url="https://graph.microsoft.com/v1.0",
    # Pas d'endpoint de révocation programmatique côté Microsoft (l'utilisateur
    # révoque via myaccount.microsoft.com) — suppression locale seule (D9).
    revoke_endpoint=None,
    authorization_extra_params={},
    # Graph throttle par mailbox (~10 000 requêtes / 10 min) ; budget prudent.
    requests_per_minute=600,
    # Subscriptions Graph sur les messages : ~3 jours max, renouvelées par PATCH.
    subscription_ttl_hours=3 * 24,
)
