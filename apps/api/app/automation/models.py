"""Activation des modules par tenant — CONTROL-PLANE (Phase 7 T3, décision D3).

`tenant_modules` est de la GOUVERNANCE de plateforme (qui a souscrit quel module —
future facturation §2), pas de la donnée métier : elle est lue par le scheduler
AVANT de poser un contexte tenant (fan-out, D4) et par le montage à chaque requête
(`require_module_enabled`). La mettre en DB tenant forcerait le scheduler à ouvrir N
bases juste pour savoir quoi faire. Aucune donnée métier ici — compatible §3.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import ControlPlaneBase


class TenantModule(ControlPlaneBase):
    __tablename__ = "tenant_modules"
    __table_args__ = (UniqueConstraint("tenant_id", "module_name", name="uq_tenant_module"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    # Slug du module au registre (`app.automation.registry`).
    module_name: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean(), default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
