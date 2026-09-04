"""Persist storefront customer ownership for explicit recovery consent.

Revision ID: 0017_add_checkout_customer
Revises: 0016_harden_sentinel_control_plane
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_add_checkout_customer"
down_revision = "0016_harden_sentinel_control_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("checkout_orders", sa.Column("customer_id", sa.String(length=128), nullable=True))
    op.create_index("ix_checkout_orders_customer_id", "checkout_orders", ["customer_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_checkout_orders_customer_id", table_name="checkout_orders")
    op.drop_column("checkout_orders", "customer_id")
