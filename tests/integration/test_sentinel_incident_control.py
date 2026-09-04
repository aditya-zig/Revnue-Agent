from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.tables import (
    ActionEvent,
    Customer,
    IncidentPaymentEvent,
    IncidentRecoveryCase,
    PaymentEvent,
    PaymentIncident,
    RecoveryCase,
)
from app.domain.enums import CaseState, EvidenceSource, PaymentEventType, PaymentStatus
from app.domain.models import NormalizedPaymentEvent
from app.domain.state_machine import apply_event
from app.main import create_app

NOW = datetime(2026, 9, 4, 6, 0, tzinfo=UTC)
AMOUNT = 249900


class OfflineIncidentProvider:
    requested_model = "offline-test"

    def generate(self, snapshot: dict):
        raise ValueError("force deterministic fallback")


class ProviderReference(str):
    provider_id: str

    def __new__(cls, value: str, provider_id: str):
        instance = str.__new__(cls, value)
        instance.provider_id = provider_id
        return instance


def _payment_link(amount: int, idempotency_key: str) -> ProviderReference:
    assert amount == AMOUNT
    return ProviderReference(
        f"https://rzp.test/{idempotency_key}",
        provider_id=f"plink_{idempotency_key}",
    )


def _seed_incident(app, suffix: str) -> str:
    incident_id = f"incident_{suffix}"
    case_id = f"case_{suffix}"
    payment_id = f"pay_{suffix}"
    obligation_reference = f"order_{suffix}"
    event_id = f"evt_failed_{suffix}"
    with app.state.session_factory() as session:
        session.add(
            Customer(
                customer_id=f"customer_{suffix}",
                tenure_days=90,
                successful_payments=4,
                prior_failures=1,
                preferred_method="card",
                consent=True,
                locale="en-IN",
            )
        )
        session.add(
            RecoveryCase(
                case_id=case_id,
                customer_id=f"customer_{suffix}",
                payment_id=payment_id,
                obligation_reference=obligation_reference,
                amount_at_risk=AMOUNT,
                state=CaseState.DETECTED,
                attempts=0,
                opened_at=NOW,
            )
        )
        session.add(
            PaymentEvent(
                event_id=event_id,
                provider_event_id=f"provider_failed_{suffix}",
                event_type=PaymentEventType.FAILED,
                payment_id=payment_id,
                obligation_reference=obligation_reference,
                merchant_order_reference=obligation_reference,
                provider_order_id=obligation_reference,
                customer_id=f"customer_{suffix}",
                amount=AMOUNT,
                currency="INR",
                method="card",
                status=PaymentStatus.FAILED,
                error_source="issuer",
                error_step="payment_authorization",
                error_code="BANK_SERVER_DOWN",
                error_reason="temporary provider outage",
                occurred_at=NOW,
                provider=EvidenceSource.RAZORPAY_TEST.value,
                source_kind=EvidenceSource.RAZORPAY_TEST,
                authenticity_verified=True,
                raw_hash="d" * 64,
                raw_body=None,
            )
        )
        session.add(
            PaymentIncident(
                incident_id=incident_id,
                state="detected",
                opened_at=NOW,
                updated_at=NOW,
                resolved_at=None,
                detection_version="test-detector-v1",
                cohort_filter={"method": "card"},
                baseline_metrics={"failure_rate": 0.05},
                observed_metrics={"failure_rate": 0.25},
                affected_attempt_count=1,
                estimated_amount_at_risk=AMOUNT,
                confidence=0.95,
                detection_evidence_json={
                    "healthy_peer_cohorts": [],
                    "failed_peer_cohorts": [],
                    "claim_class": "ESTIMATED",
                },
                provenance_summary_json={"razorpay_test": 1},
            )
        )
        session.flush()
        session.add(IncidentRecoveryCase(incident_id=incident_id, case_id=case_id))
        session.add(IncidentPaymentEvent(incident_id=incident_id, event_id=event_id))
        session.commit()
    return incident_id


def _app(database_url: str):
    return create_app(
        database_url=database_url,
        policy_now=lambda: NOW,
        create_payment_link=_payment_link,
        razorpay_key_id="rzp_test_control",
        incident_analysis_provider=OfflineIncidentProvider(),
        sentinel_owner_actor_id="demo_business_owner",
    )


@pytest.mark.asyncio
async def test_stale_context_invalidates_approved_recommendation(database_url: str) -> None:
    app = _app(database_url)
    incident_id = _seed_incident(app, "stale")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        investigated = await client.post(
            f"/api/v1/incidents/{incident_id}/investigate",
            json={"idempotency_key": "investigate-stale"},
        )
        assert investigated.status_code == 200
        recommendation = investigated.json()["recommendation"]
        assert investigated.json()["control_state"] == "needs_approval"
        assert recommendation["recommended_action"] is not None

        approved = await client.post(
            f"/api/v1/incidents/{incident_id}/approve",
            json={"action": "escalate", "approved": True},
        )
        assert approved.status_code == 201
        assert approved.json()["action"] == recommendation["recommended_action"]

        with app.state.session_factory() as session:
            customer = session.get(Customer, "customer_stale")
            assert customer is not None
            customer.consent = False
            session.commit()

        executed = await client.post(f"/api/v1/incidents/{incident_id}/execute")
        assert executed.status_code == 409
        assert "stale_recommendation_context" in executed.json()["detail"]

    execution_key = f"incident-exec:{recommendation['recommendation_id']}"
    with app.state.session_factory() as session:
        action = session.scalar(
            select(ActionEvent).where(ActionEvent.idempotency_key == execution_key)
        )
        assert action is None


@pytest.mark.asyncio
async def test_execution_is_idempotent_and_provider_capture_is_money_authority(
    database_url: str,
) -> None:
    app = _app(database_url)
    incident_id = _seed_incident(app, "provider")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        investigated = await client.post(
            f"/api/v1/incidents/{incident_id}/investigate",
            json={"idempotency_key": "investigate-provider"},
        )
        assert investigated.status_code == 200
        recommendation = investigated.json()["recommendation"]

        approved = await client.post(f"/api/v1/incidents/{incident_id}/approve")
        assert approved.status_code == 201
        approved_again = await client.post(f"/api/v1/incidents/{incident_id}/approve")
        assert approved_again.status_code == 200

        executed = await client.post(f"/api/v1/incidents/{incident_id}/execute")
        assert executed.status_code == 201
        executed_again = await client.post(f"/api/v1/incidents/{incident_id}/execute")
        assert executed_again.status_code == 200

        before_capture = await client.get(f"/api/v1/incidents/{incident_id}/control")
        assert before_capture.status_code == 200
        assert before_capture.json()["control_state"] == "awaiting_outcome"
        assert before_capture.json()["actual_recovered_amount_paise"] == 0

    execution_key = f"incident-exec:{recommendation['recommendation_id']}"
    with app.state.session_factory() as session:
        actions = session.scalars(
            select(ActionEvent).where(ActionEvent.idempotency_key == execution_key)
        ).all()
        assert len(actions) == 1
        capture = NormalizedPaymentEvent(
            event_id="evt_capture_provider",
            provider_event_id="provider_capture_provider",
            event_type=PaymentEventType.CAPTURED,
            payment_id="pay_provider",
            obligation_reference="order_provider",
            merchant_order_reference="order_provider",
            provider_order_id="order_provider",
            customer_id="customer_provider",
            amount=AMOUNT,
            currency="INR",
            method="card",
            status=PaymentStatus.CAPTURED,
            occurred_at=NOW,
            provider=EvidenceSource.RAZORPAY_TEST.value,
            source_kind=EvidenceSource.RAZORPAY_TEST,
            authenticity_verified=True,
            raw_hash="e" * 64,
        )
        apply_event(session, capture)
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        recovered = await client.get(f"/api/v1/incidents/{incident_id}/control")
    assert recovered.status_code == 200
    assert recovered.json()["control_state"] == "recovered"
    assert recovered.json()["actual_recovered_amount_paise"] == AMOUNT
    assert recovered.json()["actual_recovered_claim_tag"] == "TEST MODE"
