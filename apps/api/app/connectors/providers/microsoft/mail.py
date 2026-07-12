"""MailCapability Microsoft 365 (Graph REST via httpx, décision D2, Phase 5 T5).

Deux APIs très différentes derrière la même capability : la validation de
l'abstraction (§5). Les fonctions de mapping sont pures et testées sur fixtures.
"""

import re
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.capabilities import EmailDraft, EmailMessage
from app.connectors.client_base import graph_request
from app.connectors.tenant_models import ConnectorConnection

_FRACTION_RE = re.compile(r"\.(\d{6})\d+")


def parse_graph_datetime(value: str) -> datetime:
    """Graph renvoie jusqu'à 7 décimales de secondes — fromisoformat en veut ≤ 6."""
    trimmed = _FRACTION_RE.sub(r".\1", value)
    parsed = datetime.fromisoformat(trimmed)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def address_of(node: Any) -> str:
    if isinstance(node, dict):
        email = cast(dict[str, Any], node).get("emailAddress", {})
        if isinstance(email, dict):
            return str(cast(dict[str, Any], email).get("address", ""))
    return ""


def graph_message_to_email(payload: dict[str, Any]) -> EmailMessage:
    """Mapping Microsoft Graph → modèle normalisé (mêmes champs que Gmail)."""
    received = payload.get("receivedDateTime")
    return EmailMessage(
        provider_raw_id=str(payload["id"]),
        subject=str(payload.get("subject", "")),
        sender=address_of(payload.get("from")),
        recipients=[
            address
            for address in (
                address_of(node) for node in cast(list[Any], payload.get("toRecipients", []))
            )
            if address
        ],
        snippet=str(payload.get("bodyPreview", "")),
        received_at=parse_graph_datetime(str(received)) if received else None,
    )


def draft_to_graph_message(draft: EmailDraft) -> dict[str, Any]:
    message: dict[str, Any] = {
        "subject": draft.subject,
        "body": {"contentType": "Text", "content": draft.body_text},
        "toRecipients": [{"emailAddress": {"address": address}} for address in draft.to],
    }
    if draft.cc:
        message["ccRecipients"] = [{"emailAddress": {"address": address}} for address in draft.cc]
    return {"message": message, "saveToSentItems": True}


class MicrosoftMail:
    def __init__(self, session: AsyncSession, connection: ConnectorConnection) -> None:
        self._session = session
        self._connection = connection

    async def list_messages(
        self, since: datetime | None = None, folder: str = "inbox", limit: int = 50
    ) -> list[EmailMessage]:
        params: dict[str, str] = {
            "$top": str(min(limit, 100)),
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,from,toRecipients,bodyPreview,receivedDateTime",
        }
        if since is not None:
            stamp = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            params["$filter"] = f"receivedDateTime ge {stamp}"
        # Pagination absorbée : suivre @odata.nextLink jusqu'à `limit`.
        messages: list[dict[str, Any]] = []
        path: str | None = f"/me/mailFolders/{folder}/messages"
        next_params: dict[str, str] | None = params
        while path is not None and len(messages) < limit:
            response = await graph_request(
                self._session, self._connection, "GET", path, params=next_params
            )
            payload: dict[str, Any] = response.json()
            messages.extend(payload.get("value", []))
            next_link = payload.get("@odata.nextLink")
            if isinstance(next_link, str):
                from app.connectors.registry import get_provider

                base = get_provider(self._connection.provider).api_base_url
                path, next_params = next_link.removeprefix(base), None
            else:
                path = None
        return [graph_message_to_email(item) for item in messages[:limit]]

    async def get_message(self, message_id: str) -> EmailMessage:
        response = await graph_request(
            self._session, self._connection, "GET", f"/me/messages/{message_id}"
        )
        return graph_message_to_email(response.json())

    async def send_message(self, draft: EmailDraft) -> str | None:
        await graph_request(
            self._session,
            self._connection,
            "POST",
            "/me/sendMail",
            json=draft_to_graph_message(draft),
        )
        # Graph `sendMail` répond 202 sans corps : pas d'id de message émis.
        return None
