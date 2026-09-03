from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, inspect, select, text

from app.db.tables import (
    IncidentAuditEvent,
    IncidentPaymentEvent,
    IncidentRecoveryCase,
    PaymentEvent,
    PaymentIncident,
    RecoveryCase,
)
from app.domain.enums import EvidenceSource, IncidentState
from app.domain.incidents import transition_incident
from app.domain.models import NormalizedPaymentEvent
from app.main import create_app

NOW = datetime(2026, 9, 4, 0, 0, tzinfo=UTC)


def _incident() -> PaymentIncident:
    return PaymentIncident(
        incident_id="incident_upi_001",
        state=IncidentState.DETECTED,
        opened_at=NOW,
        updated_at=NOW,
        resolved_at=None,
        detection_version="sentinel-detector-v1",
        cohort_filter={"method": "upi", "provider": "razorpay_test"},
        baseline_metrics={"success_rate": 0.92, "attempts": 100},
        observed_metrics={"success_rate": 0.58, "attempts": 24},
        affected_attempt_count=24,
        estimated_amount_at_risk=4620000,
        confidence=0.97,
        detection_evidence_json={"threshold": 0.2, "drop": 0.34},
        provenance_summary_json={"razorpay_test": 1},
        analysis_reference=None,
        recommendation_reference=None,
    )


def _event() -> PaymentEvent:
    return PaymentEvent(
        event_id="evt_signed_001",
        provider_event_id="signed_001",
        event_type="payment.failed",
        payment_id="pay_001",
        obligation_reference="order_001",
        merchant_order_reference="merchant_order_001",
        provider_order_id="order_001",
        customer_id="cust_pseudo_001",
        amount=249900,
        currency="INR",
        method="upi",
        status="failed",
        error_source="bank",
        error_step="payment_authorization",
        error_code="GATEWAY_ERROR",
        error_reason="temporary provider failure",
        occurred_at=NOW,
        provider="razorpay_test",
        source_kind=EvidenceSource.RAZORPAY_TEST,
        authenticity_verified=True,
        raw_hash="a" * 64,
        raw_body=b"sensitive provider body must not enter incident evidence",
    )


def _case() -> RecoveryCase:
    return RecoveryCase(
        case_id="case_order_001",
        customer_id="cust_pseudo_001",
        payment_id="pay_001",
        obligation_reference="order_001",
        amount_at_risk=249900,
        state="detected",
        attempts=0,
        opened_at=NOW,
    )


def test_razorpay_normalization_keeps_truthful_source_and_identity() -> None:
    payload = {
        "id": "provider_event_001",
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_001",
                    "order_id": "order_001",
                    "amount": 249900,
                    "currency": "INR",
                    "method": "upi",
                    "status": "failed",
                    "created_at": 1788480000,
                    "notes": {
                        "obligation_reference": "merchant_order_001",
                        "customer_id": "cust_pseudo_001",
                    },
                }
            }
        },
    }

    event = NormalizedPaymentEvent.from_razorpay(
        payload,
        "b" * 64,
        authenticity_verified=True,
    )

    assert event.source_kind == EvidenceSource.RAZORPAY_TEST
    assert event.authenticity_verified is True
    assert event.provider_order_id == "order_001"
    assert event.merchant_order_reference == "merchant_order_001"
    assert event.obligation_reference == "order_001"
    assert event.provider_event_id == "provider_event_001"


def test_incident_lifecycle_is_audited_and_rejects_state_skips(database_url: str) -> None:
    app = create_app(database_url=database_url)
    with app.state.session_factory() as session:
        incident = _incident()
        session.add(incident)
        session.flush()
        transition_incident(session, incident, IncidentState.INVESTIGATING)
        session.commit()

        audit = session.scalars(select(IncidentAuditEvent)).all()
        assert incident.state == IncidentState.INVESTIGATING
        assert len(audit) == 1
        assert audit[0].event_type == "incident.investigating"
        assert audit[0].payload == {"from": "detected", "to": "investigating"}

        with pytest.raises(ValueError, match="cannot transition"):
            transition_incident(session, incident, IncidentState.RESOLVED)


@pytest.mark.asyncio
async def test_incident_links_detail_and_correlation_are_idempotent(database_url: str) -> None:
    app = create_app(database_url=database_url)
    with app.state.session_factory() as session:
        session.add_all([_incident(), _event(), _case()])
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first_link = await client.post(
            "/api/v1/incidents/incident_upi_001/links",
            json={"event_id": "evt_signed_001", "case_id": "case_order_001"},
        )
        second_link = await client.post(
            "/api/v1/incidents/incident_upi_001/links",
            json={"event_id": "evt_signed_001", "case_id": "case_order_001"},
        )
        listing = await client.get("/api/v1/incidents")
        detail = await client.get("/api/v1/incidents/incident_upi_001")
        correlation = await client.get(
            "/api/v1/correlation/payment",
            params={"provider_order_id": "order_001"},
        )

    assert first_link.status_code == 200
    assert first_link.json() == {
        "incident_id": "incident_upi_001",
        "event_linked": True,
        "case_linked": True,
    }
    assert second_link.json() == {
        "incident_id": "incident_upi_001",
        "event_linked": False,
        "case_linked": False,
    }
    assert listing.status_code == 200
    assert listing.json()[0]["incident_id"] == "incident_upi_001"

    payload = detail.json()
    assert detail.status_code == 200
    assert payload["linked_event_ids"] == ["evt_signed_001"]
    assert payload["case_chain"][0]["case_id"] == "case_order_001"
    evidence = payload["evidence_bundle"]
    assert evidence["model_hypotheses"] == []
    assert evidence["evidence"][0]["source_kind"] == "razorpay_test"
    assert evidence["evidence"][0]["authenticity_verified"] is True
    assert evidence["evidence"][0]["evidence_hash"] == "a" * 64
    assert "raw_body" not in evidence["evidence"][0]
    assert "customer_id" not in evidence["evidence"][0]

    assert correlation.json() == {
        "event_ids": ["evt_signed_001"],
        "case_ids": ["case_order_001"],
        "incident_ids": ["incident_upi_001"],
    }

    with app.state.session_factory() as session:
        assert len(session.scalars(select(IncidentPaymentEvent)).all()) == 1
        assert len(session.scalars(select(IncidentRecoveryCase)).all()) == 1


def test_incident_migration_upgrades_existing_payment_events(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'pre_sentinel.db'}"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0013_durable_checkout_order_recovery")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO payment_events (
                    event_id, provider_event_id, event_type, payment_id,
                    obligation_reference, customer_id, amount, currency, method,
                    status, error_source, error_step, error_code, error_reason,
                    occurred_at, provider, raw_hash, raw_body
                ) VALUES (
                    :event_id, :provider_event_id, :event_type, :payment_id,
                    :obligation_reference, NULL, :amount, 'INR', 'upi',
                    'failed', NULL, NULL, NULL, NULL,
                    :occurred_at, 'razorpay_test', :raw_hash, NULL
                )
                """
            ),
            {
                "event_id": "legacy_evt",
                "provider_event_id": "legacy_provider_evt",
                "event_type": "payment.failed",
                "payment_id": "legacy_pay",
                "obligation_reference": "legacy_order",
                "amount": 10000,
                "occurred_at": NOW,
                "raw_hash": "c" * 64,
            },
        )

    command.upgrade(config, "head")

    table_names = set(inspect(engine).get_table_names())
    assert {
        "payment_incidents",
        "incident_payment_events",
        "incident_recovery_cases",
        "incident_audit_events",
    }.issubset(table_names)
    column_names = {column["name"] for column in inspect(engine).get_columns("payment_events")}
    assert {
        "source_kind",
        "merchant_order_reference",
        "provider_order_id",
        "authenticity_verified",
    }.issubset(column_names)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT source_kind, provider_order_id, authenticity_verified "
                "FROM payment_events WHERE event_id = 'legacy_evt'"
            )
        ).one()
    assert row.source_kind == "razorpay_test"
    assert row.provider_order_id == "legacy_order"
    assert bool(row.authenticity_verified) is False
