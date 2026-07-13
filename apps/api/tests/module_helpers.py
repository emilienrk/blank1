"""Helpers partagés des tests du runtime d'automatisation et des modules (Phase 7)."""

import uuid
from datetime import UTC, datetime

from app.automation import service as module_service
from app.automation.models import TenantModule
from app.connectors.capabilities import EmailMessage
from app.core.db import get_control_sessionmaker
from app.tenancy.models import Tenant


async def enable_module_row(tenant: Tenant, module_name: str, *, enabled: bool = True) -> None:
    """Active (ou désactive) un module pour un tenant directement en control-plane,
    sans passer par le contrôle de capabilities (setup de test)."""
    async with get_control_sessionmaker()() as session:
        session.add(TenantModule(tenant_id=tenant.id, module_name=module_name, enabled=enabled))
        await session.commit()
    module_service.reset_state_cache()


class FakeMail:
    """Doublure de `MailCapability` : retourne des messages fixes, sans provider réel."""

    def __init__(self, subjects: list[str]) -> None:
        self._subjects = subjects

    async def list_messages(
        self, since: datetime | None = None, folder: str = "inbox", limit: int = 50
    ) -> list[EmailMessage]:
        return [
            EmailMessage(
                provider_raw_id=str(uuid.uuid4()),
                subject=subject,
                sender="someone@example.test",
                received_at=datetime.now(UTC),
            )
            for subject in self._subjects
        ]

    async def get_message(self, message_id: str) -> EmailMessage:  # pragma: no cover - inutilisé
        raise NotImplementedError

    async def send_message(self, draft: object) -> str | None:  # pragma: no cover - inutilisé
        raise NotImplementedError
