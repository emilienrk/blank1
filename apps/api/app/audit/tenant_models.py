"""Journal d'audit applicatif — donnée du client, scopée tenant (ADR 0001).

Append-only par design : aucune colonne modifiable après insertion, aucune route de
modification/suppression. `actor_label` est figé au moment du fait (décision D3) :
les users vivent en control-plane et peuvent changer d'email ou disparaître — le
journal doit rester lisible tel quel, sans jointure.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.tenancy.tenant_base import TenantScoped


class AuditEvent(Base, TenantScoped):
    __tablename__ = "audit_events"
    # Index re-scopés (tenant_id, …) : toutes les lectures portent le filtre tenant.
    __table_args__ = (
        Index("ix_audit_events_tenant_action", "tenant_id", "action"),
        # Curseur de pagination stable (décision D4) : (occurred_at, id) par tenant.
        Index("ix_audit_events_tenant_cursor", "tenant_id", "occurred_at", "id"),
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
