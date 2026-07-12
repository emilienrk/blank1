# TestClient/httpx exposent des membres partiellement typés.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Capabilities normalisées (Phase 5 T4/T5).

get_capability retourne l'implémentation du bon provider ; capability non
supportée/non consentie → erreur typée ; mapping Gmail → EmailMessage et
Graph → EmailMessage produisent des modèles identiques (mêmes champs).
"""

import httpx
import pytest

from app.connectors.capabilities import (
    CalendarCapability,
    CapabilityError,
    EmailDraft,
    MailCapability,
    get_capability,
    granted_capabilities,
)
from app.connectors.providers.google.mail import draft_to_gmail_raw, gmail_message_to_email
from app.connectors.providers.microsoft.mail import draft_to_graph_message, graph_message_to_email
from app.connectors.registry import override_provider
from app.connectors.tenant_models import (
    ConnectionStatus,
    ConnectorConnection,
    ConnectorProvider,
)
from app.core.config import Settings
from app.tenancy.context import tenant_context
from app.tenancy.engine_manager import get_engine_manager
from app.tenancy.provisioning import provision_tenant
from tests.conftest import requires_postgres
from tests.connector_helpers import (
    GOOGLE_SCOPES,
    create_connection,
    ctx_for,
    fake_microsoft_manifest,
    install_fake_transport,
    install_fake_valkey,
    reset_connector_throttle,
)
from tests.helpers import reset_db_engines

pytestmark = requires_postgres


# --- Mapping normalisé (pur, sans DB) : preuve de l'abstraction (§5) ---

GMAIL_PAYLOAD = {
    "id": "gmail-1",
    "internalDate": "1752316800000",
    "snippet": "Bonjour, ceci est un test",
    "payload": {
        "headers": [
            {"name": "Subject", "value": "Réunion projet"},
            {"name": "From", "value": "Alice <alice@example.com>"},
            {"name": "To", "value": "Bob <bob@example.com>, carol@example.com"},
        ]
    },
}

GRAPH_PAYLOAD = {
    "id": "graph-1",
    "subject": "Réunion projet",
    "bodyPreview": "Bonjour, ceci est un test",
    "receivedDateTime": "2026-07-12T10:00:00Z",
    "from": {"emailAddress": {"address": "alice@example.com"}},
    "toRecipients": [
        {"emailAddress": {"address": "bob@example.com"}},
        {"emailAddress": {"address": "carol@example.com"}},
    ],
}


def test_gmail_and_graph_map_to_identical_normalized_message() -> None:
    from_gmail = gmail_message_to_email(GMAIL_PAYLOAD)
    from_graph = graph_message_to_email(GRAPH_PAYLOAD)

    # Deux APIs très différentes, mêmes champs normalisés (hors provider_raw_id).
    assert from_gmail.subject == from_graph.subject == "Réunion projet"
    assert from_gmail.sender == from_graph.sender == "alice@example.com"
    assert (
        from_gmail.recipients
        == from_graph.recipients
        == [
            "bob@example.com",
            "carol@example.com",
        ]
    )
    assert from_gmail.snippet == from_graph.snippet
    # provider_raw_id garde la trace de l'objet source, spécifique au provider.
    assert from_gmail.provider_raw_id == "gmail-1"
    assert from_graph.provider_raw_id == "graph-1"


def test_drafts_encode_for_each_provider() -> None:
    draft = EmailDraft(to=["bob@example.com"], subject="Salut", body_text="Corps")
    raw = draft_to_gmail_raw(draft)
    assert isinstance(raw, str) and raw  # base64url MIME
    graph = draft_to_graph_message(draft)
    assert graph["message"]["toRecipients"][0]["emailAddress"]["address"] == "bob@example.com"
    assert graph["message"]["subject"] == "Salut"


# --- get_capability : dispatch et garde-fous ---


async def test_get_capability_returns_provider_implementation(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant, provider=ConnectorProvider.GOOGLE)
    await reset_db_engines()

    with tenant_context(ctx_for(tenant)):
        async with get_engine_manager().session(ctx_for(tenant)) as session:
            loaded = await session.get(ConnectorConnection, connection.id)
            assert loaded is not None
            mail = get_capability(session, loaded, MailCapability)
            calendar = get_capability(session, loaded, CalendarCapability)
            from app.connectors.providers.google.calendar import GoogleCalendar
            from app.connectors.providers.google.mail import GoogleMail

            assert isinstance(mail, GoogleMail)
            assert isinstance(calendar, GoogleCalendar)


async def test_get_capability_rejects_unconsented_capability(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    # Connexion mail-only : calendar non consenti.
    mail_only = [s for s in GOOGLE_SCOPES if "calendar" not in s]
    connection = await create_connection(tenant, scopes=mail_only)
    await reset_db_engines()

    with tenant_context(ctx_for(tenant)):
        async with get_engine_manager().session(ctx_for(tenant)) as session:
            loaded = await session.get(ConnectorConnection, connection.id)
            assert loaded is not None
            assert granted_capabilities(loaded) == frozenset({"mail"})
            with pytest.raises(CapabilityError, match="non consentie"):
                get_capability(session, loaded, CalendarCapability)


async def test_get_capability_rejects_non_active_connection(db_env: Settings) -> None:
    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant, status=ConnectionStatus.NEEDS_RECONSENT)
    await reset_db_engines()

    with tenant_context(ctx_for(tenant)):
        async with get_engine_manager().session(ctx_for(tenant)) as session:
            loaded = await session.get(ConnectorConnection, connection.id)
            assert loaded is not None
            with pytest.raises(CapabilityError, match="non active"):
                get_capability(session, loaded, MailCapability)


# --- Un appel réel de capability à travers l'enveloppe (Microsoft Graph mocké) ---


async def test_microsoft_mail_list_messages_through_capability(
    db_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_valkey(monkeypatch)
    tenant = await provision_tenant("acme", "ACME")
    connection = await create_connection(tenant, provider=ConnectorProvider.MICROSOFT)
    await reset_db_engines()

    def handler(request: httpx.Request) -> httpx.Response:
        if "/messages" in str(request.url):
            return httpx.Response(200, json={"value": [GRAPH_PAYLOAD]})
        return httpx.Response(404)

    install_fake_transport(monkeypatch, handler)

    try:
        with override_provider(fake_microsoft_manifest()), tenant_context(ctx_for(tenant)):
            async with get_engine_manager().session(ctx_for(tenant)) as session:
                loaded = await session.get(ConnectorConnection, connection.id)
                assert loaded is not None
                mail = get_capability(session, loaded, MailCapability)
                messages = await mail.list_messages(limit=10)
                assert len(messages) == 1
                assert messages[0].subject == "Réunion projet"
    finally:
        reset_connector_throttle()
