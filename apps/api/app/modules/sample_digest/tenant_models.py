"""Table du module `sample_digest` en DB TENANT (Phase 7 T5/T6, décision D5).

Les tables d'un module vivent dans la MetaData tenant existante, préfixées
`<name>_` (ici `sample_digest_`), et leurs migrations rejoignent l'arbre tenant
unique (Phase 1). Un module désactivé garde ses tables : le schéma tenant reste
identique pour tous les tenants, seul le comportement diffère.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.tenancy.tenant_base import TenantBase


class SampleDigestDigest(TenantBase):
    __tablename__ = "sample_digest_digests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # Nombre d'emails couverts par le résumé (métrique, jamais leur contenu).
    message_count: Mapped[int] = mapped_column(Integer(), default=0)
    # Résumé produit par le provider IA de la politique du tenant.
    summary: Mapped[str] = mapped_column(Text())
