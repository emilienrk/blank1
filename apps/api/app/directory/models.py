"""Identités globales (control-plane).

Un email peut appartenir à plusieurs tenants (plan global §3) : `users` porte
l'identité globale, `memberships` le lien user x tenant x rôle. Les credentials
(mot de passe, TOTP, identités OAuth) vivent dans `app.auth.models`.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import ControlPlaneBase


class User(ControlPlaneBase):
    __tablename__ = "users"
    __table_args__ = (Index("ux_users_email_lower", text("lower(email)"), unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(255), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Membership(ControlPlaneBase):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id", name="ux_memberships_user_tenant"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    # Rôle tenant : owner/admin/member (décision D6 Phase 2, `app.auth.permissions`).
    # Colonne texte : les rôles custom (Phase 7+) s'ajouteront sans migration destructive.
    role: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
