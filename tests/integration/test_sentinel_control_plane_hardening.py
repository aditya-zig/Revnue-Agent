import hashlib
import sqlite3
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, inspect, select

from app.db.tables import ActionEvent, Customer, Decision, Outcome, PaymentEvent, RecoveryCase
from app.domain.enums import CaseState, EvidenceSource, PaymentEventType, PaymentStatus
from app.domain.models import NormalizedPaymentEvent
from app.domain.state_machine import apply_event
from app.main import _database_readiness, create_app
from app.recovery.actions import execute_action

NOW = datetime(2026, 9, 4, 6, 0, tzinfo=UTC)


class ProviderReference(str):
    provider_id: str

    def __new__(cls, value: str, provider_id: str):
        instance = str.__new__(cls, value)
        instance.provider_id = provider_id
        return instance


def _case(case_id: str = "case_control") -> RecoveryCase:
    return RecoveryCase(
        case_id=case_id,
        customer_id="cust_control",
        payment_id="pay_control",
        obligation_reference="order_control",
        amount_at_risk=249900,
        state=CaseState.ELIGIBLE,
        attempts=0,
        opened_at=NOW,
    )


def test_execute_action_does_not_reuse_unrelated_historical_approval(database_url: str) -> None:
    app = create_app(database_url=database_url, policy_now=lambda: NOW)
    with app.state.session_factory() as session:
        case = _case()
        session.add(Customer(customer_id="cust_control", consent=True))
        session.add(case)
        historical_key = "historical-decision-key"
        session.add(
            Decision(
                decision_id=f"decision_{hashlib.sha256(historical_key.encode()).hexdigest()}",
                case_id=case.case_id,
                policy_version="v1",
                model_version="test-model",
                allowed_actions=["payment_link"],
                selected_action="payment_link",
                expected_value=100,
                reason_json={
                    "evidence": {},
                    "selection_source": "fallback",
                    "rejection": None,
                    "approval": {"required": True, "granted": True},
                },
            )
        )
        session.commit()

        with pytest.raises(PermissionError, match="approval_required"):
            execute_action(
                session,
                case,
                "payment_link",
                "new-execution-key",
                NOW,
                21,
                8,
                lambda amount, key: ProviderReference(
                    f"https://rzp.test/{key}", provider_id="plink_exact_approval"
                ),
            )


@pytest.mark.asyncio
async def test_mock_pay_reply_records_intent_but_not_recovered_money(database_url: str) -> None:
    app = create_app(database_url=database_url, policy_now=lambda: NOW)
    with app.state.session_factory() as session:
        case = _case("case_mock_pay")
        case.state = CaseState.AWAITING_OUTCOME
        session.add(Customer(customer_id="cust_control", consent=True))
        session.add(case)
        session.add(
            ActionEvent(
                action_id="action_mock_pay",
                case_id=case.case_id,
                idempotency_key="mock-pay-action",
                tool="contact",
                input_hash="a" * 64,
                status="completed",
                provider_reference="mock_contact_reference",
                executed_at=NOW,
            )
        )
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/mock-inbox/mock_contact_reference/reply",
            json={"reply": "pay"},
        )

    assert response.status_code == 200
    with app.state.session_factory() as session:
        case = session.get(RecoveryCase, "case_mock_pay")
        assert case is not None
        assert case.state == CaseState.AWAITING_OUTCOME
        assert session.scalar(select(Outcome).where(Outcome.case_id == case.case_id)) is None
        intent = session.scalar(
            select(ActionEvent).where(ActionEvent.action_id == "action_mock_pay")
        )
        assert intent is not None
        assert intent.reply == "pay"



def test_unverified_razorpay_test_capture_cannot_create_recovered_outcome(database_url: str) -> None:
    app = create_app(database_url=database_url, policy_now=lambda: NOW)
    with app.state.session_factory() as session:
        session.add(Customer(customer_id="cust_control", consent=True))
        session.add(_case("case_unverified_capture"))
        session.add(
            PaymentEvent(
                event_id="evt_failed_control",
                provider_event_id="provider_evt_failed_control",
                event_type=PaymentEventType.FAILED,
                payment_id="pay_control",
                obligation_reference="order_control",
                merchant_order_reference="merchant_order_control",
                provider_order_id="order_control",
                customer_id="cust_control",
                amount=249900,
                currency="INR",
                method="card",
                status=PaymentStatus.FAILED,
                error_source="issuer",
                error_step="payment_authorization",
                error_code="BANK_SERVER_DOWN",
                error_reason="temporary provider failure",
                occurred_at=NOW,
                provider=EvidenceSource.RAZORPAY_TEST.value,
                source_kind=EvidenceSource.RAZORPAY_TEST,
                authenticity_verified=True,
                raw_hash="b" * 64,
                raw_body=None,
            )
        )
        session.commit()

        capture = NormalizedPaymentEvent(
            event_id="evt_unverified_capture",
            provider_event_id="provider_evt_unverified_capture",
            event_type=PaymentEventType.CAPTURED,
            payment_id="pay_control",
            obligation_reference="order_control",
            merchant_order_reference="merchant_order_control",
            provider_order_id="order_control",
            customer_id="cust_control",
            amount=249900,
            currency="INR",
            method="card",
            status=PaymentStatus.CAPTURED,
            occurred_at=NOW,
            provider=EvidenceSource.RAZORPAY_TEST.value,
            source_kind=EvidenceSource.RAZORPAY_TEST,
            authenticity_verified=False,
            raw_hash="c" * 64,
        )
        apply_event(session, capture)
        session.flush()

        assert (
            session.scalar(
                select(Outcome).where(Outcome.case_id == "case_unverified_capture")
            )
            is None
        )


@pytest.mark.asyncio
async def test_browser_role_header_cannot_grant_sentinel_owner_authority(database_url: str) -> None:
    app = create_app(database_url=database_url, sentinel_owner_actor_id=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/incidents/missing/approve",
            headers={"X-Reroute-Role": "business_owner"},
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "business owner role required"


def test_persistent_legacy_schema_is_reported_as_incompatible(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE checkout_orders (checkout_id TEXT PRIMARY KEY);
            CREATE TABLE leak_findings (finding_id TEXT PRIMARY KEY);
            CREATE TABLE payment_events (event_id TEXT PRIMARY KEY);
            CREATE TABLE recovery_cases (case_id TEXT PRIMARY KEY);
            """
        )
    app = create_app(database_url=f"sqlite:///{path}")
    assert _database_readiness(app) == "schema_incompatible"


def test_sentinel_incident_link_foreign_keys_have_indexes(database_url: str) -> None:
    engine = create_engine(database_url)
    database_inspector = inspect(engine)
    payment_indexes = {
        index["name"] for index in database_inspector.get_indexes("incident_payment_events")
    }
    case_indexes = {
        index["name"] for index in database_inspector.get_indexes("incident_recovery_cases")
    }
    assert "ix_incident_payment_events_event_id" in payment_indexes
    assert "ix_incident_recovery_cases_case_id" in case_indexes
