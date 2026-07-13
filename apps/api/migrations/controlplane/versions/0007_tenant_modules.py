"""Activation des modules par tenant (Phase 7 T3) : tenant_modules — control-plane
(gouvernance, décision D3). Lue par le scheduler avant tout contexte tenant et par
le montage à chaque requête.

Revision ID: 0007_tenant_modules
Revises: 0006_ai_gateway
Create Date: 2026-07-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_tenant_modules"
down_revision: str | None = "0006_ai_gateway"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_modules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("module_name", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "module_name", name="uq_tenant_module"),
    )
    op.create_index("ix_tenant_modules_tenant_id", "tenant_modules", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_tenant_modules_tenant_id", table_name="tenant_modules")
    op.drop_table("tenant_modules")
