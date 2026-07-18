"""Logique du module `sample_digest` (Phase 7 T6).

La tâche : via `MailCapability` (Phase 5), liste les emails des dernières 24 h ; via
`AIGateway.chat` (Phase 6, `module="sample_digest"` — la ventilation par module du §6
devient réelle), produit un résumé ; stocke le digest dans la table tenant
`sample_digest_digests` ; audite `sample_digest.digest_generated`.

Ne consomme QUE des briques socle : capabilities, gateway, audit, session tenant.
Aucun accès direct aux APIs providers, aucun import d'un autre module (invariants de
phase n°3 et D8).
"""

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import ChatRequest, Message, get_gateway
from app.audit.service import record_audit_event
from app.connectors.capabilities import MailCapability, get_capability, granted_capabilities
from app.connectors.registry import CAPABILITY_MAIL
from app.connectors.tenant_models import ConnectionStatus, ConnectorConnection
from app.connectors.webhooks import ConnectorEvent
from app.modules.sample_digest.tenant_models import SampleDigestDigest
from app.tenancy.context import current_tenant
from app.tenancy.session import tenant_session

logger = structlog.get_logger()

MODULE_NAME = "sample_digest"
TASK_NAME = "sample_digest.daily_digest"
DIGEST_ACTION = "sample_digest.digest_generated"
_LOOKBACK = timedelta(hours=24)

_SYSTEM_PROMPT = (
    "Tu es un assistant qui résume en français, en 3 puces maximum, les sujets "
    "saillants d'une liste d'emails reçus. Sois factuel et concis."
)


class SampleDigestError(RuntimeError):
    """Aucune connexion mail active pour produire le digest."""


async def _active_mail_connection(session: AsyncSession) -> ConnectorConnection | None:
    """Première connexion ACTIVE dont la capability mail est consentie."""
    connections = (
        await session.scalars(
            select(ConnectorConnection).where(ConnectorConnection.status == ConnectionStatus.ACTIVE)
        )
    ).all()
    for connection in connections:
        if CAPABILITY_MAIL in granted_capabilities(connection):
            return connection
    return None


def _build_prompt(subjects: list[str]) -> str:
    lines = "\n".join(f"- {subject}" for subject in subjects)
    return f"Voici les objets des emails reçus ces dernières 24 h :\n{lines}"


async def generate_digest(session: AsyncSession) -> SampleDigestDigest:
    """Produit et stocke un digest pour le tenant courant (session tenant fournie).

    L'appelant commit. Lève `SampleDigestError` si aucune connexion mail active
    (la désactivation d'un connecteur après activation du module est possible)."""
    connection = await _active_mail_connection(session)
    if connection is None:
        msg = "Aucune connexion mail active — impossible de générer le digest."
        raise SampleDigestError(msg)

    mail: MailCapability = get_capability(session, connection, MailCapability)
    since = datetime.now(UTC) - _LOOKBACK
    messages = await mail.list_messages(since=since)

    if messages:
        subjects = [message.subject for message in messages]
        result = await get_gateway().chat(
            ChatRequest(
                messages=[
                    Message(role="system", content=_SYSTEM_PROMPT),
                    Message(role="user", content=_build_prompt(subjects)),
                ],
                module=MODULE_NAME,
            )
        )
        summary = result.content
    else:
        summary = "Aucun nouvel email sur les dernières 24 heures."

    digest = SampleDigestDigest(summary=summary, message_count=len(messages))
    session.add(digest)
    await session.flush()
    await record_audit_event(
        session,
        action=DIGEST_ACTION,
        resource_type="sample_digest_digest",
        resource_id=str(digest.id),
        payload={"message_count": len(messages)},
    )
    logger.info("sample_digest_generated", message_count=len(messages))
    return digest


async def generate_digest_task(tenant_id: uuid.UUID) -> None:
    """Tâche périodique (signature `(tenant_id) -> None`) : le scheduler a déjà posé
    le contexte tenant ; on ouvre une session, on génère, on commit."""
    current_tenant()  # fail-fast : le scheduler doit avoir posé le contexte
    async with tenant_session() as session:
        await generate_digest(session)
        await session.commit()


async def on_mail_event(event: ConnectorEvent) -> None:
    """Abonnement au hook connecteur (Phase 5, D7) : trivial — trace la réception d'un
    événement mail (un vrai module y déclencherait une regénération incrémentale)."""
    logger.info(
        "sample_digest_mail_event",
        connection_id=str(event.connection_id),
        change_type=event.change_type,
    )
