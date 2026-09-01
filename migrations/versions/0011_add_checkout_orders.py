"""Persist server-owned storefront orders.

Revision ID: 0011_add_checkout_orders
Revises: 0010_merge_finding_analysis_and_shared_payment_ids
Create Date: 2026-09-01 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_add_checkout_orders"
down_revision = "0010_merge_finding_analysis_and_shared_payment_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "checkout_orders",
        sa.Column("checkout_id", sa.String(length=128), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("provider_order_id", sa.String(length=128), nullable=True, unique=True),
        sa.Column("obligation_reference", sa.String(length=128), nullable=True, unique=True),
        sa.Column("product_code", sa.String(length=64), nullable=False),
        sa.Column("product_name", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payment_id", sa.String(length=128), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_checkout_orders_idempotency_key", "checkout_orders", ["idempotency_key"])
    op.create_index(
        "ix_checkout_orders_provider_order_id", "checkout_orders", ["provider_order_id"]
    )
    op.create_index(
        "ix_checkout_orders_obligation_reference", "checkout_orders", ["obligation_reference"]
    )
    op.create_index("ix_checkout_orders_payment_id", "checkout_orders", ["payment_id"])


def downgrade() -> None:
    op.drop_index("ix_checkout_orders_payment_id", table_name="checkout_orders")
    op.drop_index("ix_checkout_orders_obligation_reference", table_name="checkout_orders")
    op.drop_index("ix_checkout_orders_provider_order_id", table_name="checkout_orders")
    op.drop_index("ix_checkout_orders_idempotency_key", table_name="checkout_orders")
    op.drop_table("checkout_orders")
