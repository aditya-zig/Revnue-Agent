"""Persist immutable deterministic finding analyses.

Revision ID: 0008_add_finding_analysis_records
Revises: 0007_add_decision_time
Create Date: 2026-08-28 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_add_finding_analysis_records"
down_revision = "0007_add_decision_time"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finding_analyses",
        sa.Column("analysis_id", sa.String(length=128), primary_key=True),
        # This is provenance only. LeakFinding rows are replaced by detector runs,
        # so this deliberately is not a foreign key.
        sa.Column("source_finding_id", sa.String(length=128), nullable=False, index=True),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("impact_paise", sa.Integer(), nullable=False),
        sa.Column("recoverable_impact_paise", sa.Integer(), nullable=False),
        sa.Column("claim_tag", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_finding_analyses_source_snapshot",
        "finding_analyses",
        ["source_finding_id", "snapshot_hash"],
    )
    op.execute(
        "CREATE TRIGGER finding_analyses_no_update BEFORE UPDATE ON finding_analyses "
        "BEGIN SELECT RAISE(ABORT, 'finding analyses are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER finding_analyses_no_delete BEFORE DELETE ON finding_analyses "
        "BEGIN SELECT RAISE(ABORT, 'finding analyses are immutable'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER finding_analyses_no_delete")
    op.execute("DROP TRIGGER finding_analyses_no_update")
    op.drop_index("ix_finding_analyses_source_snapshot", table_name="finding_analyses")
    op.drop_table("finding_analyses")
