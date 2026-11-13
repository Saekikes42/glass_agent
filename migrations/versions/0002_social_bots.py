"""Social identities and durable bot inbox.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_platform_identities",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prefs", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("platform", "external_id"),
        sa.UniqueConstraint("platform", "user_id"),
    )
    op.create_table(
        "bot_updates",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("event_key", sa.String(200), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("platform", "event_key"),
    )
    op.create_index("ix_bot_updates_pending", "bot_updates", ["platform", "status", "available_at"])


def downgrade() -> None:
    op.drop_table("bot_updates")
    op.drop_table("user_platform_identities")
