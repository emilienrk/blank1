"""Manifest Google Workspace (Phase 5 T2) : Gmail + Calendar.

`access_type=offline` + `prompt=consent` garantissent l'émission d'un refresh
token à CHAQUE consentement (sans `prompt=consent`, Google n'en renvoie qu'au
premier — le callback échouerait explicitement, voir `oauth.py`).

Spécificité webhooks : Google Calendar notifie par « channels » (~7 jours, non
renouvelables — on en recrée un avant expiration). Gmail ne pousse que via
Cloud Pub/Sub (pas de webhook direct) : la subscription mail Google est hors
périmètre de cette phase, documentée au README.
"""

from typing import Any

from app.connectors.registry import (
    CAPABILITY_CALENDAR,
    CAPABILITY_MAIL,
    ProviderManifest,
)
from app.connectors.tenant_models import ConnectorProvider


def _account_label(payload: dict[str, Any]) -> str | None:
    email = payload.get("email")
    return email if isinstance(email, str) else None


GOOGLE_MANIFEST = ProviderManifest(
    provider=ConnectorProvider.GOOGLE,
    authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
    token_endpoint="https://oauth2.googleapis.com/token",
    account_info_endpoint="https://openidconnect.googleapis.com/v1/userinfo",
    parse_account_label=_account_label,
    base_scopes=("openid", "email"),
    capability_scopes={
        CAPABILITY_MAIL: (
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ),
        CAPABILITY_CALENDAR: ("https://www.googleapis.com/auth/calendar",),
    },
    api_base_url="https://www.googleapis.com",
    revoke_endpoint="https://oauth2.googleapis.com/revoke",
    authorization_extra_params={"access_type": "offline", "prompt": "consent"},
    # Gmail plafonne à 250 unités de quota/s/utilisateur ; on reste très en deçà.
    requests_per_minute=240,
    # Channels Calendar : ~7 jours max — renouvelés (recréés) avant expiration.
    subscription_ttl_hours=7 * 24,
)
