"""Schéma tenant initial : tenant_settings (clé/valeur).

Revision ID: 0001_tenant
Revises:
Create Date: 2026-07-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_tenant"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_settings",
        sa.Column("key", sa.String(length=100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("tenant_settings")
