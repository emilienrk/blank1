"""Catalogue des tenants (table non scopée — c'est elle que `tenant_id` référence)."""

import enum
import re
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}$")

# Sous-domaines réservés par la plateforme : jamais attribuables à un tenant.
RESERVED_SLUGS = frozenset({"www", "api", "admin", "app", "staging", "status", "grafana"})


class TenantState(enum.StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [str(member.value) for member in enum_cls]


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    state: Mapped[TenantState] = mapped_column(
        Enum(
            TenantState,
            name="tenant_state",
            native_enum=False,
            length=20,
            values_callable=_enum_values,
        ),
        default=TenantState.ACTIVE,
    )
    # Préparation facturation (plan global §2) : rien d'autre que ce champ.
    plan: Mapped[str] = mapped_column(String(50), default="standard", server_default="standard")
    # Soft-delete (ADR 0002) : un tenant marqué ici est invisible partout (résolution
    # HTTP, fan-out beat, webhooks) mais ses données restent en base.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def validate_slug(slug: str) -> str:
    """Valide un slug de tenant (sous-domaine public : regex stricte + liste réservée)."""
    if not SLUG_RE.fullmatch(slug):
        msg = (
            f"Slug invalide : {slug!r} — attendu ^[a-z][a-z0-9-]{{1,38}}$ "
            "(minuscules, chiffres, tirets, 2 à 39 caractères)"
        )
        raise ValueError(msg)
    if slug in RESERVED_SLUGS:
        msg = f"Slug réservé par la plateforme : {slug!r}"
        raise ValueError(msg)
    return slug
