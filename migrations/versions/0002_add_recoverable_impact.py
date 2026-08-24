"""Store recoverable impact on leak findings.

Revision ID: 0002_add_recoverable_impact
Revises: 0001_initial_schema
Create Date: 2026-08-24 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_add_recoverable_impact"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leak_findings",
        sa.Column("recoverable_impact", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("leak_findings", "recoverable_impact")
