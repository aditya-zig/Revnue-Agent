"""Persist operator policy controls and mock inbox replies.

Revision ID: 0006_add_operator_controls
Revises: 0005_add_payment_exceptions
Create Date: 2026-08-27 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_add_operator_controls"
down_revision = "0005_add_payment_exceptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("action_events", sa.Column("reply", sa.String(length=32)))
    op.add_column("action_events", sa.Column("replied_at", sa.DateTime(timezone=True)))
    op.create_table(
        "policy_configurations",
        sa.Column("configuration_id", sa.String(length=32), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("quiet_hours_start", sa.Integer(), nullable=False),
        sa.Column("quiet_hours_end", sa.Integer(), nullable=False),
        sa.Column("contact_limit", sa.Integer(), nullable=False),
        sa.Column("kill_switch", sa.Boolean(), nullable=False),
        sa.Column("mock_identity", sa.String(length=128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "policy_change_audits",
        sa.Column("audit_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("actor_role", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("policy_change_audits")
    op.drop_table("policy_configurations")
    op.drop_column("action_events", "replied_at")
    op.drop_column("action_events", "reply")
