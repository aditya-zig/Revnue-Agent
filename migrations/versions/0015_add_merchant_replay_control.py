"""Add durable merchant replay control without deleting immutable evidence.

Revision ID: 0015_add_merchant_replay_control
Revises: 0014_add_sentinel_incident_foundation
Create Date: 2026-09-04 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_add_merchant_replay_control"
down_revision = "0014_add_sentinel_incident_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merchant_replay_controls",
        sa.Column("replay_id", sa.String(length=64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("scenario", sa.String(length=32), nullable=False),
        sa.Column("cursor", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("active_run_id", sa.String(length=128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("generation >= 0", name="ck_merchant_replay_generation_nonnegative"),
        sa.CheckConstraint("seed >= 0", name="ck_merchant_replay_seed_nonnegative"),
        sa.CheckConstraint("cursor >= 0", name="ck_merchant_replay_cursor_nonnegative"),
        sa.PrimaryKeyConstraint("replay_id"),
        sa.UniqueConstraint("active_run_id"),
    )
    op.create_index(
        "ix_merchant_replay_controls_active_run_id",
        "merchant_replay_controls",
        ["active_run_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_merchant_replay_controls_active_run_id",
        table_name="merchant_replay_controls",
    )
    op.drop_table("merchant_replay_controls")
