"""Catalogue des tenants (control-plane).

Le catalogue ne stocke JAMAIS d'URL de connexion ni de credentials (décision D3) :
seulement `db_name` et l'alias logique `db_host` ; les credentials viennent de l'env.
"""

import enum
import re
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import ControlPlaneBase

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}$")

# Sous-domaines réservés par la plateforme : jamais attribuables à un tenant.
RESERVED_SLUGS = frozenset({"www", "api", "admin", "app", "staging", "status", "grafana"})


class TenantState(enum.StrEnum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    FAILED = "failed"
    # Effacement RGPD demandé (Phase 4 T5, décision D2) : le tenant est déjà
    # inaccessible (`resolve_tenant` refuse comme `suspended`) pendant le délai de
    # grâce ; `execute_pending_erasures` droppe la base à son échéance.
    PENDING_DELETION = "pending_deletion"


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [str(member.value) for member in enum_cls]


class Tenant(ControlPlaneBase):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    db_name: Mapped[str] = mapped_column(String(63), unique=True)
    # Alias logique du serveur hôte (§8.7 du plan global) — `default` = serveur principal.
    db_host: Mapped[str] = mapped_column(String(255), default="default", server_default="default")
    state: Mapped[TenantState] = mapped_column(
        Enum(
            TenantState,
            name="tenant_state",
            native_enum=False,
            length=20,
            values_callable=_enum_values,
        ),
        default=TenantState.PROVISIONING,
    )
    # Préparation facturation (plan global §2) : rien d'autre que ce champ.
    plan: Mapped[str] = mapped_column(String(50), default="standard", server_default="standard")
    # Horodatage de la demande d'effacement RGPD (Phase 4 T5) — null hors PENDING_DELETION.
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def validate_slug(slug: str) -> str:
    """Valide un slug de tenant (invariant I6 : rien d'autre n'atteint un nom de DB)."""
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


def db_name_for_slug(slug: str, prefix: str) -> str:
    """Nom de DB dérivé d'un slug validé (les tirets deviennent des underscores)."""
    return prefix + validate_slug(slug).replace("-", "_")
