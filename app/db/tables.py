from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CheckoutOrder(Base):
    __tablename__ = "checkout_orders"

    checkout_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    provider_order_id: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True, index=True
    )
    provider_receipt: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    obligation_reference: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True, index=True
    )
    product_code: Mapped[str] = mapped_column(String(64))
    product_name: Mapped[str] = mapped_column(String(128))
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(32))
    creating_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class PaymentEvent(Base):
    __tablename__ = "payment_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider_event_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payment_id: Mapped[str] = mapped_column(String(128), index=True)
    obligation_reference: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    error_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str] = mapped_column(String(64))
    raw_hash: Mapped[str] = mapped_column(String(64))
    raw_body: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class RecoveryCase(Base):
    __tablename__ = "recovery_cases"

    case_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payment_id: Mapped[str] = mapped_column(String(128), index=True)
    obligation_reference: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    amount_at_risk: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    stop_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class PaymentException(Base):
    __tablename__ = "payment_exceptions"

    exception_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("recovery_cases.case_id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="open")
    evidence_json: Mapped[dict] = mapped_column(JSON)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution_evidence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Customer(Base):
    __tablename__ = "customers"

    customer_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenure_days: Mapped[int] = mapped_column(Integer, default=0)
    successful_payments: Mapped[int] = mapped_column(Integer, default=0)
    prior_failures: Mapped[int] = mapped_column(Integer, default=0)
    preferred_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consent: Mapped[bool] = mapped_column(default=False)
    locale: Mapped[str | None] = mapped_column(String(32), nullable=True)


class LeakFinding(Base):
    __tablename__ = "leak_findings"

    finding_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    detector_version: Mapped[str] = mapped_column(String(64))
    cohort_filter: Mapped[dict] = mapped_column(JSON)
    baseline_rate: Mapped[float] = mapped_column()
    observed_rate: Mapped[float] = mapped_column()
    impact: Mapped[int] = mapped_column(Integer)
    recoverable_impact: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column()
    evidence_json: Mapped[dict] = mapped_column(JSON)


class FindingAnalysis(Base):
    __tablename__ = "finding_analyses"

    analysis_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # Provenance only: detector runs replace LeakFinding rows, so this is not a FK.
    source_finding_id: Mapped[str] = mapped_column(String(128), index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    result_json: Mapped[dict] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(64))
    requested_model: Mapped[str] = mapped_column(String(128))
    resolved_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_generation_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(64))
    usage_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_usage_json: Mapped[dict] = mapped_column(JSON)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallback_used: Mapped[bool] = mapped_column(default=True)
    impact_paise: Mapped[int] = mapped_column(Integer)
    recoverable_impact_paise: Mapped[int] = mapped_column(Integer)
    claim_tag: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Decision(Base):
    __tablename__ = "decisions"

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("recovery_cases.case_id"), index=True)
    policy_version: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    allowed_actions: Mapped[list] = mapped_column(JSON)
    selected_action: Mapped[str] = mapped_column(String(64))
    expected_value: Mapped[int] = mapped_column(Integer)
    reason_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ActionEvent(Base):
    __tablename__ = "action_events"

    action_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("recovery_cases.case_id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    tool: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    provider_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_reference_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reply: Mapped[str | None] = mapped_column(String(32), nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Outcome(Base):
    __tablename__ = "outcomes"

    outcome_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("recovery_cases.case_id"), unique=True)
    recovered: Mapped[bool] = mapped_column(default=False)
    recovered_amount: Mapped[int] = mapped_column(Integer, default=0)
    contact_cost: Mapped[int] = mapped_column(Integer, default=0)
    discount_cost: Mapped[int] = mapped_column(Integer, default=0)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(64))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("recovery_cases.case_id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class PolicyConfiguration(Base):
    __tablename__ = "policy_configurations"

    configuration_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    quiet_hours_start: Mapped[int] = mapped_column(Integer)
    quiet_hours_end: Mapped[int] = mapped_column(Integer)
    contact_limit: Mapped[int] = mapped_column(Integer)
    kill_switch: Mapped[bool] = mapped_column(default=False)
    mock_identity: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class PolicyChangeAudit(Base):
    __tablename__ = "policy_change_audits"

    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer)
    actor_role: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
