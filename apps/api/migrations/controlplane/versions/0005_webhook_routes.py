"""Routage des webhooks connecteurs (Phase 5 T1, décision D6) : webhook_routes,
routage uniquement — aucune donnée métier ni token.

Revision ID: 0005_webhook_routes
Revises: 0004_gdpr_erasure
Create Date: 2026-07-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_webhook_routes"
down_revision: str | None = "0004_gdpr_erasure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_routes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("route_key", sa.String(length=64), nullable=False, unique=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # UUID d'une connector_connection (DB tenant) — pas de FK inter-bases.
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("webhook_routes")
