"""Capabilities normalisées (Phase 5 T4) — LE contrat consommé par les modules.

Règle absolue (§5 du plan global) : les modules consomment les capabilities,
jamais les APIs propriétaires. Les modèles normalisés ne portent que les champs
communs aux providers + `provider_raw_id` pour retrouver l'objet source.
`get_capability` retourne l'implémentation du provider de la connexion, ou une
erreur explicite si la capability n'est pas supportée/consentie.
"""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.registry import (
    CAPABILITY_CALENDAR,
    CAPABILITY_MAIL,
    get_provider,
)
from app.connectors.tenant_models import ConnectionStatus, ConnectorConnection, ConnectorProvider


class CapabilityError(RuntimeError):
    """Capability non supportée par le provider ou non consentie par la connexion."""


# --- Modèles normalisés (champs communs uniquement + provider_raw_id) ---


class EmailMessage(BaseModel):
    provider_raw_id: str
    subject: str
    sender: str
    recipients: list[str] = Field(default_factory=list)
    snippet: str = ""
    received_at: datetime | None = None


class EmailDraft(BaseModel):
    to: list[str]
    cc: list[str] = Field(default_factory=list)
    subject: str
    body_text: str


class CalendarEvent(BaseModel):
    provider_raw_id: str
    title: str
    description: str | None = None
    location: str | None = None
    starts_at: datetime
    ends_at: datetime
    attendees: list[str] = Field(default_factory=list)


class CalendarEventDraft(BaseModel):
    title: str
    description: str | None = None
    location: str | None = None
    starts_at: datetime
    ends_at: datetime
    attendees: list[str] = Field(default_factory=list)


# --- Protocols typés ---


class MailCapability(Protocol):
    async def list_messages(
        self, since: datetime | None = None, folder: str = "inbox", limit: int = 50
    ) -> list[EmailMessage]: ...

    async def get_message(self, message_id: str) -> EmailMessage: ...

    async def send_message(self, draft: EmailDraft) -> str | None:
        """Envoie le brouillon ; retourne l'id provider du message émis quand le
        provider le fournit (Graph `sendMail` n'en renvoie pas)."""
        ...


class CalendarCapability(Protocol):
    async def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]: ...

    async def create_event(self, event: CalendarEventDraft) -> CalendarEvent: ...


CAPABILITY_NAMES: dict[type, str] = {
    MailCapability: CAPABILITY_MAIL,
    CalendarCapability: CAPABILITY_CALENDAR,
}


def granted_capabilities(connection: ConnectorConnection) -> frozenset[str]:
    """Capabilities effectivement consenties : celles dont TOUS les scopes du
    manifest sont couverts par les scopes accordés à la connexion."""
    manifest = get_provider(connection.provider)
    granted = set(connection.scopes)
    return frozenset(
        capability
        for capability, scopes in manifest.capability_scopes.items()
        if granted.issuperset(scopes)
    )


def get_capability[C](
    session: AsyncSession, connection: ConnectorConnection, capability: type[C]
) -> C:
    """Implémentation du provider pour la capability demandée, ou erreur explicite."""
    name = CAPABILITY_NAMES.get(capability)
    if name is None:
        msg = f"Capability inconnue : {capability!r}"
        raise CapabilityError(msg)
    if connection.status is not ConnectionStatus.ACTIVE:
        msg = f"Connexion {connection.id} non active ({connection.status.value})."
        raise CapabilityError(msg)
    manifest = get_provider(connection.provider)
    if name not in manifest.capabilities:
        msg = f"Capability {name!r} non supportée par {connection.provider.value}."
        raise CapabilityError(msg)
    if name not in granted_capabilities(connection):
        msg = (
            f"Capability {name!r} non consentie par la connexion {connection.id} "
            "(scopes insuffisants — re-consentement requis)."
        )
        raise CapabilityError(msg)
    factory = _implementations()[(connection.provider, name)]
    return factory(session, connection)  # type: ignore[no-any-return]


def _implementations() -> dict[tuple[ConnectorProvider, str], type]:
    # Import paresseux : les implémentations importent les modèles de ce module.
    from app.connectors.providers.google.calendar import GoogleCalendar
    from app.connectors.providers.google.mail import GoogleMail
    from app.connectors.providers.microsoft.calendar import MicrosoftCalendar
    from app.connectors.providers.microsoft.mail import MicrosoftMail

    return {
        (ConnectorProvider.GOOGLE, CAPABILITY_MAIL): GoogleMail,
        (ConnectorProvider.GOOGLE, CAPABILITY_CALENDAR): GoogleCalendar,
        (ConnectorProvider.MICROSOFT, CAPABILITY_MAIL): MicrosoftMail,
        (ConnectorProvider.MICROSOFT, CAPABILITY_CALENDAR): MicrosoftCalendar,
    }
