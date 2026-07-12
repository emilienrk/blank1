"""Gateway IA (Phase 6 T1) : politiques par tenant + événements d'usage + agrégats
journaliers — control-plane (§6). Aucun prompt/complétion, uniquement des métriques.

Revision ID: 0006_ai_gateway
Revises: 0005_webhook_routes
Create Date: 2026-07-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_ai_gateway"
down_revision: str | None = "0005_webhook_routes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_ai_policies",
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("default_provider", sa.String(length=20), nullable=True),
        sa.Column("default_model", sa.String(length=100), nullable=True),
        sa.Column("allowed_providers", sa.JSON(), nullable=False),
        sa.Column("zero_retention", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("monthly_token_quota", sa.BigInteger(), nullable=True),
        sa.Column("hard_limit_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fallback_provider", sa.String(length=20), nullable=True),
        sa.Column("fallback_model", sa.String(length=100), nullable=True),
        # BYOK chiffré (KeyProvider) — préparé, jamais exposé (décision D7).
        sa.Column("byok_keys_enc", sa.LargeBinary(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "ai_usage_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("module", sa.String(length=50), nullable=False, server_default="core"),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_microeur", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("price_version", sa.String(length=20), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ok", "error", "timeout", name="ai_usage_status", native_enum=False, length=10),
            nullable=False,
        ),
        sa.Column("error_kind", sa.String(length=50), nullable=True),
    )
    op.create_index("ix_ai_usage_events_occurred_at", "ai_usage_events", ["occurred_at"])
    op.create_index("ix_ai_usage_events_tenant_id", "ai_usage_events", ["tenant_id"])

    op.create_table(
        "ai_usage_daily",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(length=20), primary_key=True),
        sa.Column("model", sa.String(length=100), primary_key=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_microeur", sa.BigInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("ai_usage_daily")
    op.drop_index("ix_ai_usage_events_tenant_id", table_name="ai_usage_events")
    op.drop_index("ix_ai_usage_events_occurred_at", table_name="ai_usage_events")
    op.drop_table("ai_usage_events")
    op.drop_table("tenant_ai_policies")
