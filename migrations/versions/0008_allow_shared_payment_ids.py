"""Allow multiple obligations to reference one payment ID.

Revision ID: 0008_allow_shared_payment_ids
Revises: 0007_add_decision_time
Create Date: 2026-08-31 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_allow_shared_payment_ids"
down_revision = "0007_add_decision_time"
branch_labels = None
depends_on = None


def _recovery_cases_table(with_payment_unique: bool) -> sa.Table:
    metadata = sa.MetaData()
    constraints: list[sa.Constraint] = []
    if with_payment_unique:
        constraints.append(sa.UniqueConstraint("payment_id"))
    table = sa.Table(
        "recovery_cases",
        metadata,
        sa.Column("case_id", sa.String(length=128), primary_key=True),
        sa.Column("customer_id", sa.String(length=128)),
        sa.Column("payment_id", sa.String(length=128), nullable=False),
        sa.Column("obligation_reference", sa.String(length=128)),
        sa.Column("amount_at_risk", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stop_reason", sa.Text()),
        *constraints,
    )
    sa.Index("ix_recovery_cases_payment_id", table.c.payment_id)
    sa.Index("ix_recovery_cases_obligation_reference", table.c.obligation_reference)
    return table


def upgrade() -> None:
    with op.batch_alter_table(
        "recovery_cases",
        recreate="always",
        copy_from=_recovery_cases_table(with_payment_unique=False),
    ):
        pass


def downgrade() -> None:
    with op.batch_alter_table(
        "recovery_cases",
        recreate="always",
        copy_from=_recovery_cases_table(with_payment_unique=False),
    ) as batch_op:
        batch_op.create_unique_constraint("uq_recovery_cases_payment_id", ["payment_id"])
