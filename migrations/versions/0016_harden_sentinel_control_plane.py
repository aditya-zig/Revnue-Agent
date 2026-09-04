"""Harden Sentinel control-plane correlation lookups.

Revision ID: 0016_harden_sentinel_control_plane
Revises: 0015_add_merchant_replay_control
"""

from alembic import op

revision = "0016_harden_sentinel_control_plane"
down_revision = "0015_add_merchant_replay_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_incident_payment_events_event_id",
        "incident_payment_events",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        "ix_incident_recovery_cases_case_id",
        "incident_recovery_cases",
        ["case_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_incident_recovery_cases_case_id",
        table_name="incident_recovery_cases",
    )
    op.drop_index(
        "ix_incident_payment_events_event_id",
        table_name="incident_payment_events",
    )
