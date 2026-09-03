"""Add Sentinel incident, provenance, and correlation foundation.

Revision ID: 0014_add_sentinel_incident_foundation
Revises: 0013_durable_checkout_order_recovery
Create Date: 2026-09-04 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_add_sentinel_incident_foundation"
down_revision = "0013_durable_checkout_order_recovery"
branch_labels = None
depends_on = None


INCIDENT_STATES = (
    "detected",
    "investigating",
    "actionable",
    "recovery_in_progress",
    "monitoring",
    "resolved",
)


def upgrade() -> None:
    op.add_column(
        "payment_events",
        sa.Column(
            "source_kind",
            sa.String(length=32),
            nullable=False,
            server_default="simulated_merchant",
        ),
    )
    op.add_column(
        "payment_events",
        sa.Column("merchant_order_reference", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "payment_events",
        sa.Column("provider_order_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "payment_events",
        sa.Column(
            "authenticity_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index("ix_payment_events_source_kind", "payment_events", ["source_kind"])
    op.create_index(
        "ix_payment_events_merchant_order_reference",
        "payment_events",
        ["merchant_order_reference"],
    )
    op.create_index(
        "ix_payment_events_provider_order_id",
        "payment_events",
        ["provider_order_id"],
    )

    op.execute(
        sa.text(
            "UPDATE payment_events SET source_kind = CASE "
            "WHEN provider = 'razorpay_test' THEN 'razorpay_test' "
            "WHEN provider = 'mock' THEN 'mock' "
            "ELSE 'simulated_merchant' END"
        )
    )
    op.execute(
        sa.text(
            "UPDATE payment_events SET provider_order_id = obligation_reference "
            "WHERE provider = 'razorpay_test' AND obligation_reference IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE payment_events SET merchant_order_reference = obligation_reference "
            "WHERE provider != 'razorpay_test' AND obligation_reference IS NOT NULL"
        )
    )

    op.create_table(
        "payment_incidents",
        sa.Column("incident_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detection_version", sa.String(length=64), nullable=False),
        sa.Column("cohort_filter", sa.JSON(), nullable=False),
        sa.Column("baseline_metrics", sa.JSON(), nullable=False),
        sa.Column("observed_metrics", sa.JSON(), nullable=False),
        sa.Column("affected_attempt_count", sa.Integer(), nullable=False),
        sa.Column("estimated_amount_at_risk", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("detection_evidence_json", sa.JSON(), nullable=False),
        sa.Column("provenance_summary_json", sa.JSON(), nullable=False),
        sa.Column("analysis_reference", sa.String(length=128), nullable=True),
        sa.Column("recommendation_reference", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "state IN (" + ", ".join(repr(state) for state in INCIDENT_STATES) + ")",
            name="ck_payment_incidents_state",
        ),
        sa.PrimaryKeyConstraint("incident_id"),
    )
    op.create_index("ix_payment_incidents_state", "payment_incidents", ["state"])

    op.create_table(
        "incident_payment_events",
        sa.Column("incident_id", sa.String(length=128), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["payment_incidents.incident_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["payment_events.event_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("incident_id", "event_id"),
    )
    op.create_table(
        "incident_recovery_cases",
        sa.Column("incident_id", sa.String(length=128), nullable=False),
        sa.Column("case_id", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["payment_incidents.incident_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["recovery_cases.case_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("incident_id", "case_id"),
    )
    op.create_table(
        "incident_audit_events",
        sa.Column("audit_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("incident_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["payment_incidents.incident_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    op.create_index(
        "ix_incident_audit_events_incident_id",
        "incident_audit_events",
        ["incident_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_incident_audit_events_incident_id", table_name="incident_audit_events"
    )
    op.drop_table("incident_audit_events")
    op.drop_table("incident_recovery_cases")
    op.drop_table("incident_payment_events")
    op.drop_index("ix_payment_incidents_state", table_name="payment_incidents")
    op.drop_table("payment_incidents")

    op.drop_index("ix_payment_events_provider_order_id", table_name="payment_events")
    op.drop_index(
        "ix_payment_events_merchant_order_reference", table_name="payment_events"
    )
    op.drop_index("ix_payment_events_source_kind", table_name="payment_events")
    op.drop_column("payment_events", "authenticity_verified")
    op.drop_column("payment_events", "provider_order_id")
    op.drop_column("payment_events", "merchant_order_reference")
    op.drop_column("payment_events", "source_kind")
