"""Table du module `sample_digest`, scopée tenant (Phase 7 T5/T6, décision D5).

Les tables d'un module héritent de `(Base, TenantScoped)` comme toute table
métier, préfixées `<name>_` (ici `sample_digest_`), et leurs migrations
rejoignent l'arbre Alembic unique. Un module désactivé garde ses tables : le
schéma reste identique pour tous les tenants, seul le comportement diffère.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.tenancy.tenant_base import TenantScoped


class SampleDigestDigest(Base, TenantScoped):
    __tablename__ = "sample_digest_digests"
    __table_args__ = (
        Index("ix_sample_digest_digests_tenant_generated", "tenant_id", "generated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Nombre d'emails couverts par le résumé (métrique, jamais leur contenu).
    message_count: Mapped[int] = mapped_column(Integer(), default=0)
    # Résumé produit par le provider IA de la politique du tenant.
    summary: Mapped[str] = mapped_column(Text())
