"""Store provider IDs alongside customer-facing recovery links.

Revision ID: 0012_add_provider_reference_id
Revises: 0011_add_checkout_orders
Create Date: 2026-09-01 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_add_provider_reference_id"
down_revision = "0011_add_checkout_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "action_events", sa.Column("provider_reference_id", sa.String(length=128), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("action_events", "provider_reference_id")
