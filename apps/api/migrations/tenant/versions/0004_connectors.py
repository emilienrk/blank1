"""Connecteurs externes (Phase 5 T1) : connector_connections (tokens chiffrés
KeyProvider) + connector_subscriptions (webhooks providers).

Revision ID: 0004_tenant_connectors
Revises: 0003_tenant_audit_events
Create Date: 2026-07-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_tenant_connectors"
down_revision: str | None = "0003_tenant_audit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connector_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        # UUID d'un user global (control-plane) — pas de FK inter-bases.
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("account_label", sa.String(length=320), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False, server_default="[]"),
        # Tokens chiffrés AES-256-GCM (KeyProvider) — jamais en clair (invariant Phase 5).
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=False),
        sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=False),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_connector_connections_status", "connector_connections", ["status"])

    op.create_table(
        "connector_subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("connector_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(length=20), nullable=False),
        sa.Column("provider_subscription_id", sa.String(length=255), nullable=False),
        sa.Column("resource", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # Secret d'authentification des notifications — stocké haché (sha256).
        sa.Column("client_state_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_connector_subscriptions_expires_at", "connector_subscriptions", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_connector_subscriptions_expires_at", table_name="connector_subscriptions")
    op.drop_table("connector_subscriptions")
    op.drop_index("ix_connector_connections_status", table_name="connector_connections")
    op.drop_table("connector_connections")
