"""Timestamp recovery decisions for the case timeline.

Revision ID: 0007_add_decision_time
Revises: 0006_add_operator_controls
Create Date: 2026-08-27 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_add_decision_time"
down_revision = "0006_add_operator_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("decisions", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("decisions", "created_at")
