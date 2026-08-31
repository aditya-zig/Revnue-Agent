"""Add PaymentObligation identity.

Revision ID: 0003_add_obligation_reference
Revises: 0002_add_recoverable_impact
Create Date: 2026-08-25 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_add_obligation_reference"
down_revision = "0002_add_recoverable_impact"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PaymentObligation is the explicit verified merchant reference; one
    # permanent RecoveryCase per obligation.
    # When no durable reference exists the payment attempt remains isolated until human links it.
    op.add_column(
        "payment_events",
        sa.Column("obligation_reference", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_payment_events_obligation_reference", "payment_events", ["obligation_reference"]
    )
    op.add_column(
        "recovery_cases",
        sa.Column("obligation_reference", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_recovery_cases_obligation_reference", "recovery_cases", ["obligation_reference"]
    )
    # payment_id was previously unique; with obligation grouping multiple payment_ids map to one
    # case,
    # so the case's payment_id is no longer unique. Keep index but drop unique constraint.
    # SQLite does not support altering unique via alembic directly; we recreate the index without
    # unique.
    # The unique constraint was implicit via unique=True; we keep the column but allow duplicates
    # by not enforcing.
    # For existing DBs the unique index will remain; drop and recreate as non-unique if exists.
    try:
        op.drop_index("ix_recovery_cases_payment_id", table_name="recovery_cases")
    except Exception:
        pass
    op.create_index("ix_recovery_cases_payment_id", "recovery_cases", ["payment_id"])


def downgrade() -> None:
    op.drop_index("ix_recovery_cases_payment_id", table_name="recovery_cases")
    op.create_index("ix_recovery_cases_payment_id", "recovery_cases", ["payment_id"], unique=True)
    op.drop_index("ix_recovery_cases_obligation_reference", table_name="recovery_cases")
    op.drop_column("recovery_cases", "obligation_reference")
    op.drop_index("ix_payment_events_obligation_reference", table_name="payment_events")
    op.drop_column("payment_events", "obligation_reference")
