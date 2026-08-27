"""Create the PaymentException workflow.

Revision ID: 0005_add_payment_exceptions
Revises: 0004_remove_recovery_case_payment_unique
Create Date: 2026-08-27 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_add_payment_exceptions"
down_revision = "0004_remove_recovery_case_payment_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_exceptions",
        sa.Column("exception_id", sa.String(length=128), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=128),
            sa.ForeignKey("recovery_cases.case_id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("resolution", sa.String(length=32)),
        sa.Column("resolution_evidence_json", sa.JSON()),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_payment_exceptions_case_id", "payment_exceptions", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_payment_exceptions_case_id", table_name="payment_exceptions")
    op.drop_table("payment_exceptions")
