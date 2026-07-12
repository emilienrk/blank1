"""Journal d'audit applicatif — donnée du client, en DB TENANT (plan global §7,
Phase 4 T1).

Append-only : aucune colonne modifiable après insertion, aucune route de
modification/suppression en dehors de la politique de rétention (`app.gdpr.retention`,
T6). `actor_label` est figé au moment du fait (décision D3) : les users vivent en
control-plane et peuvent changer d'email ou disparaître (effacement d'un autre
tenant) — le journal doit rester lisible tel quel, sans jointure inter-bases.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.tenancy.tenant_base import TenantBase


class AuditEvent(TenantBase):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_occurred_at", "occurred_at"),
        Index("ix_audit_events_action", "action"),
        # Curseur de pagination stable (décision D4) : (occurred_at, id).
        Index("ix_audit_events_cursor", "occurred_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # UUID d'un user global (control-plane) — null = acteur système/CLI (décision D3).
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    actor_label: Mapped[str] = mapped_column(String(320))
    # Namespacé : `core.*` (socle), `connector.*`/`module_x.*` réservés aux phases
    # suivantes (convention de nommage uniquement, zéro code spéculatif).
    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON(), default=dict)
