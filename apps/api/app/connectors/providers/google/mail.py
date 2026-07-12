"""MailCapability Google (Gmail API via google-api-python-client, Phase 5 T5).

Le SDK est synchrone : chaque `execute()` part dans le threadpool
(`run_sync_call`, décision D4) sous l'enveloppe throttle/backoff. Les fonctions
de mapping sont pures et testées sur fixtures réelles anonymisées.
"""

# google-api-python-client n'expose pas de types.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingModuleSource=false

import base64
from collections.abc import Callable
from datetime import UTC, datetime
from email.message import EmailMessage as MimeMessage
from email.utils import getaddresses, parseaddr
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import throttle
from app.connectors.capabilities import EmailDraft, EmailMessage
from app.connectors.client_base import fresh_access_token, run_sync_call
from app.connectors.registry import get_provider
from app.connectors.tenant_models import ConnectorConnection

_METADATA_HEADERS = ["Subject", "From", "To", "Date"]


def _header(payload: dict[str, Any], name: str) -> str:
    for header in payload.get("payload", {}).get("headers", []):
        if str(header.get("name", "")).lower() == name.lower():
            return str(header.get("value", ""))
    return ""


def gmail_message_to_email(payload: dict[str, Any]) -> EmailMessage:
    """Mapping Gmail → modèle normalisé (champs communs uniquement)."""
    internal_date = payload.get("internalDate")
    received_at = (
        datetime.fromtimestamp(int(internal_date) / 1000, tz=UTC) if internal_date else None
    )
    _, sender = parseaddr(_header(payload, "From"))
    recipients = [address for _, address in getaddresses([_header(payload, "To")]) if address]
    return EmailMessage(
        provider_raw_id=str(payload["id"]),
        subject=_header(payload, "Subject"),
        sender=sender,
        recipients=recipients,
        snippet=str(payload.get("snippet", "")),
        received_at=received_at,
    )


def draft_to_gmail_raw(draft: EmailDraft) -> str:
    """Brouillon normalisé → message MIME encodé base64url (format Gmail `raw`)."""
    mime = MimeMessage()
    mime["To"] = ", ".join(draft.to)
    if draft.cc:
        mime["Cc"] = ", ".join(draft.cc)
    mime["Subject"] = draft.subject
    mime.set_content(draft.body_text)
    return base64.urlsafe_b64encode(mime.as_bytes()).decode()


class GoogleMail:
    def __init__(self, session: AsyncSession, connection: ConnectorConnection) -> None:
        self._session = session
        self._connection = connection
        self._manifest = get_provider(connection.provider)

    async def _execute(self, call: Callable[[Any], Any]) -> Any:
        """Un appel Gmail : token frais, SDK en threadpool, enveloppe throttle."""
        access_token = await fresh_access_token(self._session, self._connection)

        async def attempt() -> Any:
            def sync_call() -> Any:
                from google.oauth2.credentials import Credentials

                credentials = Credentials(token=access_token)  # pyright: ignore[reportCallIssue]
                client = build("gmail", "v1", credentials=credentials, cache_discovery=False)
                try:
                    return call(client)
                except HttpError as exc:
                    raise throttle.ProviderResponseError(exc.status_code, detail="gmail") from exc

            return await run_sync_call(sync_call)

        return await throttle.run_with_backoff(self._manifest, self._connection.id, attempt)

    async def list_messages(
        self, since: datetime | None = None, folder: str = "inbox", limit: int = 50
    ) -> list[EmailMessage]:
        query = f"after:{int(since.timestamp())}" if since is not None else None

        def list_call(client: Any) -> Any:
            """Liste paginée puis lecture des métadonnées — pagination absorbée."""
            label = folder.upper()
            messages: list[dict[str, Any]] = []
            page_token: str | None = None
            while len(messages) < limit:
                response = (
                    client.users()
                    .messages()
                    .list(
                        userId="me",
                        labelIds=[label],
                        q=query,
                        maxResults=min(limit - len(messages), 100),
                        pageToken=page_token,
                    )
                    .execute()
                )
                for item in response.get("messages", []):
                    messages.append(
                        client.users()
                        .messages()
                        .get(
                            userId="me",
                            id=item["id"],
                            format="metadata",
                            metadataHeaders=_METADATA_HEADERS,
                        )
                        .execute()
                    )
                page_token = response.get("nextPageToken")
                if page_token is None:
                    break
            return messages

        payloads = await self._execute(list_call)
        return [gmail_message_to_email(payload) for payload in payloads[:limit]]

    async def get_message(self, message_id: str) -> EmailMessage:
        payload = await self._execute(
            lambda client: (
                client.users()
                .messages()
                .get(
                    userId="me", id=message_id, format="metadata", metadataHeaders=_METADATA_HEADERS
                )
                .execute()
            )
        )
        return gmail_message_to_email(payload)

    async def send_message(self, draft: EmailDraft) -> str | None:
        raw = draft_to_gmail_raw(draft)
        payload = await self._execute(
            lambda client: client.users().messages().send(userId="me", body={"raw": raw}).execute()
        )
        message_id = payload.get("id")
        return str(message_id) if message_id is not None else None
