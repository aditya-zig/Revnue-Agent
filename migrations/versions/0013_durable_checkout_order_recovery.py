"""Persist deterministic order receipts and creation leases.

Revision ID: 0013_durable_checkout_order_recovery
Revises: 0012_add_provider_reference_id
Create Date: 2026-09-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_durable_checkout_order_recovery"
down_revision = "0012_add_provider_reference_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "checkout_orders",
        sa.Column("provider_receipt", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "checkout_orders",
        sa.Column("creating_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_checkout_orders_provider_receipt",
        "checkout_orders",
        ["provider_receipt"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_checkout_orders_provider_receipt", table_name="checkout_orders")
    op.drop_column("checkout_orders", "creating_started_at")
    op.drop_column("checkout_orders", "provider_receipt")
