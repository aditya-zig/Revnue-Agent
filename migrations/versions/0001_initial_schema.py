"""Create the Phase 1 schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-23 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("customer_id", sa.String(length=128), primary_key=True),
        sa.Column("tenure_days", sa.Integer(), nullable=False),
        sa.Column("successful_payments", sa.Integer(), nullable=False),
        sa.Column("prior_failures", sa.Integer(), nullable=False),
        sa.Column("preferred_method", sa.String(length=64)),
        sa.Column("consent", sa.Boolean(), nullable=False),
        sa.Column("locale", sa.String(length=32)),
    )
    op.create_table(
        "payment_events",
        sa.Column("event_id", sa.String(length=128), primary_key=True),
        sa.Column("provider_event_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payment_id", sa.String(length=128), nullable=False),
        sa.Column("customer_id", sa.String(length=128)),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("method", sa.String(length=64)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_source", sa.String(length=64)),
        sa.Column("error_step", sa.String(length=64)),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column("error_reason", sa.Text()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("raw_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_body", sa.LargeBinary()),
    )
    op.create_index("ix_payment_events_payment_id", "payment_events", ["payment_id"])
    op.create_table(
        "recovery_cases",
        sa.Column("case_id", sa.String(length=128), primary_key=True),
        sa.Column("customer_id", sa.String(length=128)),
        sa.Column("payment_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("amount_at_risk", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stop_reason", sa.Text()),
    )
    op.create_table(
        "leak_findings",
        sa.Column("finding_id", sa.String(length=128), primary_key=True),
        sa.Column("detector_version", sa.String(length=64), nullable=False),
        sa.Column("cohort_filter", sa.JSON(), nullable=False),
        sa.Column("baseline_rate", sa.Float(), nullable=False),
        sa.Column("observed_rate", sa.Float(), nullable=False),
        sa.Column("impact", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
    )
    op.create_table(
        "decisions",
        sa.Column("decision_id", sa.String(length=128), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=128),
            sa.ForeignKey("recovery_cases.case_id"),
            nullable=False,
        ),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=64)),
        sa.Column("allowed_actions", sa.JSON(), nullable=False),
        sa.Column("selected_action", sa.String(length=64), nullable=False),
        sa.Column("expected_value", sa.Integer(), nullable=False),
        sa.Column("reason_json", sa.JSON(), nullable=False),
    )
    op.create_table(
        "action_events",
        sa.Column("action_id", sa.String(length=128), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=128),
            sa.ForeignKey("recovery_cases.case_id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_reference", sa.String(length=128)),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "outcomes",
        sa.Column("outcome_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "case_id",
            sa.String(length=128),
            sa.ForeignKey("recovery_cases.case_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("recovered", sa.Boolean(), nullable=False),
        sa.Column("recovered_amount", sa.Integer(), nullable=False),
        sa.Column("contact_cost", sa.Integer(), nullable=False),
        sa.Column("discount_cost", sa.Integer(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(length=64), nullable=False),
    )
    op.create_table(
        "audit_events",
        sa.Column("audit_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "case_id",
            sa.String(length=128),
            sa.ForeignKey("recovery_cases.case_id"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'audit events are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'audit events are immutable'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_events_no_delete")
    op.execute("DROP TRIGGER audit_events_no_update")
    op.drop_table("audit_events")
    op.drop_table("outcomes")
    op.drop_table("action_events")
    op.drop_table("decisions")
    op.drop_table("leak_findings")
    op.drop_table("recovery_cases")
    op.drop_index("ix_payment_events_payment_id", table_name="payment_events")
    op.drop_table("payment_events")
    op.drop_table("customers")
