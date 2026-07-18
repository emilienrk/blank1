"""Équipes — données du client, scopées tenant (ADR 0001).

Première vraie table métier : son CRUD HTTP prouve la pile de bout en bout
(resolve_tenant → get_tenant_session → filtre tenant_id automatique). `user_id`
référence l'UUID des identités globales — la cohérence (le user doit être membre
du tenant) est garantie par le service.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.tenancy.tenant_base import TenantScoped


class Team(Base, TenantScoped):
    __tablename__ = "teams"
    # Unicité du nom PAR tenant (ex-`unique=True` du modèle base-par-tenant).
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="ux_teams_tenant_name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text(), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TeamMember(Base, TenantScoped):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="ux_team_members_team_user"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    # UUID d'un user global — membre du tenant, vérifié par le service.
    user_id: Mapped[uuid.UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
