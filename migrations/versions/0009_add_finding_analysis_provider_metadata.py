"""Record external FindingAnalysis provider metadata.

Revision ID: 0009_add_finding_analysis_provider_metadata
Revises: 0008_add_finding_analysis_records
Create Date: 2026-08-31 00:00:00
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0009_add_finding_analysis_provider_metadata"
down_revision = "0008_add_finding_analysis_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("finding_analyses", sa.Column("provider", sa.String(length=64), nullable=True))
    op.add_column(
        "finding_analyses", sa.Column("requested_model", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "finding_analyses", sa.Column("resolved_model", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "finding_analyses",
        sa.Column("provider_generation_id", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "finding_analyses", sa.Column("prompt_version", sa.String(length=64), nullable=True)
    )
    op.add_column("finding_analyses", sa.Column("usage_json", sa.JSON(), nullable=True))
    op.add_column("finding_analyses", sa.Column("tool_usage_json", sa.JSON(), nullable=True))
    op.add_column("finding_analyses", sa.Column("failure_reason", sa.Text(), nullable=True))
    op.add_column("finding_analyses", sa.Column("fallback_used", sa.Boolean(), nullable=True))

    op.get_bind().execute(
        sa.text(
            "UPDATE finding_analyses SET provider = 'openrouter', "
            "requested_model = 'openrouter/free', "
            "prompt_version = 'deterministic-finding-analysis-v1', "
            "tool_usage_json = :tool_usage, fallback_used = 1"
        ),
        {"tool_usage": json.dumps({"requested": False, "used": False, "tools": []})},
    )
    with op.batch_alter_table("finding_analyses") as batch_op:
        batch_op.alter_column("provider", existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column(
            "requested_model", existing_type=sa.String(length=128), nullable=False
        )
        batch_op.alter_column("prompt_version", existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column("tool_usage_json", existing_type=sa.JSON(), nullable=False)
        batch_op.alter_column("fallback_used", existing_type=sa.Boolean(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("finding_analyses") as batch_op:
        batch_op.drop_column("fallback_used")
        batch_op.drop_column("failure_reason")
        batch_op.drop_column("tool_usage_json")
        batch_op.drop_column("usage_json")
        batch_op.drop_column("prompt_version")
        batch_op.drop_column("provider_generation_id")
        batch_op.drop_column("resolved_model")
        batch_op.drop_column("requested_model")
        batch_op.drop_column("provider")
