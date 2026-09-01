"""Merge the finding analysis and shared payment ID migration branches.

Revision ID: 0010_merge_finding_analysis_and_shared_payment_ids
Revises: 0009_add_finding_analysis_provider_metadata, 0008_allow_shared_payment_ids
Create Date: 2026-09-01 00:00:00
"""

revision = "0010_merge_finding_analysis_and_shared_payment_ids"
down_revision = (
    "0009_add_finding_analysis_provider_metadata",
    "0008_allow_shared_payment_ids",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
