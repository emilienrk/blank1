"""Équipes — données du client, en DB TENANT (plan global §3, Phase 2 T8).

Première vraie table métier : son CRUD HTTP prouve la pile Phase 1 de bout en
bout (resolve_tenant → get_tenant_session). `user_id` référence l'UUID des
identités globales du control-plane — pas de FK inter-bases possible, la
cohérence est garantie par le service (le user doit être membre du tenant).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.tenancy.tenant_base import TenantBase


class Team(TenantBase):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str | None] = mapped_column(Text(), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TeamMember(TenantBase):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="ux_team_members_team_user"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    # UUID d'un user global (control-plane) — membre du tenant, vérifié par le service.
    user_id: Mapped[uuid.UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
