import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.tables import Customer, Outcome, PaymentEvent, RecoveryCase
from app.main import create_app


@pytest.fixture
def app(database_url):
    return create_app(
        database_url=database_url,
        webhook_secret="test-secret",
    )


def signature(body: bytes) -> str:
    return hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_verified_failure_creates_one_case_and_audit_record(app):
    payload = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_001",
                    "amount": 249900,
                    "currency": "INR",
                    "method": "upi",
                    "status": "failed",
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "insufficient funds",
                    "created_at": 1724481000,
                    "notes": {"customer_id": "cust_001"},
                }
            }
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = {"X-Razorpay-Signature": signature(body), "Content-Type": "application/json"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/webhooks/razorpay", content=body, headers=headers)
        duplicate = await client.post("/api/v1/webhooks/razorpay", content=body, headers=headers)
        cases = await client.get("/api/v1/cases")
        audit = await client.get("/api/v1/audit/case_pay_001")

    assert first.status_code == 202
    assert first.json() == {
        "event_id": f"evt_{hashlib.sha256(body).hexdigest()}",
        "status": "accepted",
    }
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "event_id": f"evt_{hashlib.sha256(body).hexdigest()}",
        "status": "duplicate",
    }
    assert cases.json() == [
        {
            "amount_at_risk": 249900,
            "attempts": 0,
            "case_id": "case_pay_001",
            "customer_id": "cust_001",
            "payment_id": "pay_001",
            "state": "detected",
            "stop_reason": None,
        }
    ]
    assert audit.json() == [
        {
            "case_id": "case_pay_001",
            "event_type": "case.detected",
            "payload": {"payment_id": "pay_001"},
        },
        {
            "case_id": "case_pay_001",
            "event_type": "event.recorded",
            "payload": {
                "event_id": f"evt_{hashlib.sha256(body).hexdigest()}",
                "event_type": "payment.failed",
            },
        },
        {
            "case_id": "case_pay_001",
            "event_type": "event.duplicate",
            "payload": {
                "event_id": f"evt_{hashlib.sha256(body).hexdigest()}",
                "event_type": "payment.failed",
            },
        },
    ]


@pytest.mark.asyncio
async def test_webhook_rejects_a_signature_that_does_not_match_the_raw_body(app):
    body = b'{"event":"payment.failed"}'

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": "not-a-valid-signature"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid webhook signature"}


@pytest.mark.asyncio
async def test_official_failure_payload_with_empty_notes_uses_webhook_event_id(app):
    payload = {
        "entity": "event",
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_official_001",
                    "amount": 50000,
                    "currency": "INR",
                    "status": "failed",
                    "method": "upi",
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "Payment failed",
                    "error_reason": "payment_failed",
                    "created_at": 1567610214,
                    "notes": [],
                }
            }
        },
        "created_at": 1567610215,
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Event-Id": "event_official_001",
        "X-Razorpay-Signature": signature(body),
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/webhooks/razorpay", content=body, headers=headers)
        duplicate = await client.post("/api/v1/webhooks/razorpay", content=body, headers=headers)
        dashboard = await client.get("/api/v1/dashboard")

    assert first.json() == {"event_id": "evt_event_official_001", "status": "accepted"}
    assert duplicate.json() == {"event_id": "evt_event_official_001", "status": "duplicate"}
    evidence = dashboard.json()["worklist"][0]["evidence"]
    assert evidence["error_reason"] == "payment_failed"
    assert "raw_body" not in evidence


@pytest.mark.asyncio
async def test_provider_reversal_signal_opens_a_payment_exception(app):
    payload = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_reversal_001",
                    "amount": 50000,
                    "currency": "INR",
                    "status": "failed",
                    "error_reason": "payment_reversed",
                    "created_at": 1567610214,
                }
            }
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": signature(body)},
        )
        exceptions = await client.get("/api/v1/exceptions")

    assert response.status_code == 202
    assert exceptions.json()[0]["kind"] == "provider_reversal"


@pytest.mark.asyncio
async def test_repeated_failures_for_one_payment_create_distinct_events_and_one_case(app):
    def payload(created_at: int) -> bytes:
        return json.dumps(
            {
                "event": "payment.failed",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_001",
                            "amount": 249900,
                            "currency": "INR",
                            "status": "failed",
                            "created_at": created_at,
                        }
                    }
                },
            },
            separators=(",", ":"),
        ).encode()

    first_body = payload(1724481000)
    second_body = payload(1724482000)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/webhooks/razorpay",
            content=first_body,
            headers={"X-Razorpay-Signature": signature(first_body)},
        )
        second = await client.post(
            "/api/v1/webhooks/razorpay",
            content=second_body,
            headers={"X-Razorpay-Signature": signature(second_body)},
        )
        cases = await client.get("/api/v1/cases")

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["event_id"] != second.json()["event_id"]
    assert len(cases.json()) == 1


@pytest.mark.asyncio
async def test_webhook_rejects_a_modified_body_with_the_original_signature(app):
    original_body = b'{"event":"payment.failed"}'
    modified_body = b'{"event":"payment.captured"}'

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=modified_body,
            headers={"X-Razorpay-Signature": signature(original_body)},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid webhook signature"}


@pytest.mark.asyncio
async def test_webhook_hydrates_an_unknown_customer_without_granting_consent(app):
    payload = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_hydrated",
                    "amount": 249900,
                    "currency": "INR",
                    "status": "failed",
                    "created_at": 1724481000,
                    "notes": {"customer_id": "cust_hydrated"},
                }
            }
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": signature(body)},
        )
        policy = await client.get("/api/v1/cases/case_pay_hydrated/policy")

    assert response.status_code == 202
    assert "missing_identity" not in policy.json()["blocked_reasons"]["payment_link"]
    assert "missing_consent" in policy.json()["blocked_reasons"]["payment_link"]
    with app.state.session_factory() as session:
        customer = session.get(Customer, "cust_hydrated")
        assert customer is not None
        assert customer.consent is False


@pytest.mark.asyncio
async def test_captured_webhook_does_not_attribute_failure_from_another_obligation(app):
    with app.state.session_factory() as session:
        session.add_all(
            [
                Customer(customer_id="cust_obligation_a", consent=True),
                Customer(customer_id="cust_obligation_b", consent=True),
                RecoveryCase(
                    case_id="case_order_a",
                    customer_id="cust_obligation_a",
                    payment_id="pay_shared",
                    obligation_reference="order_a",
                    amount_at_risk=249900,
                    state="awaiting_outcome",
                    attempts=0,
                ),
                RecoveryCase(
                    case_id="case_order_b",
                    customer_id="cust_obligation_b",
                    payment_id="pay_shared",
                    obligation_reference="order_b",
                    amount_at_risk=249900,
                    state="awaiting_outcome",
                    attempts=0,
                ),
                PaymentEvent(
                    event_id="evt_failure_b",
                    provider_event_id="provider_failure_b",
                    event_type="payment.failed",
                    payment_id="pay_shared",
                    obligation_reference="order_b",
                    customer_id="cust_obligation_b",
                    amount=249900,
                    currency="INR",
                    status="failed",
                    occurred_at=datetime(2024, 8, 24, 6, 31, tzinfo=UTC),
                    provider="razorpay_test",
                    raw_hash="failure-b-hash",
                ),
            ]
        )
        session.commit()

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_shared",
                    "amount": 249900,
                    "currency": "INR",
                    "status": "captured",
                    "created_at": 1724481100,
                    "order_id": "order_a",
                    "notes": {"customer_id": "cust_obligation_a"},
                }
            }
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": signature(body)},
        )

    assert response.status_code == 202
    with app.state.session_factory() as session:
        assert session.scalar(select(Outcome).where(Outcome.case_id == "case_order_a")) is None
        assert session.scalar(select(Outcome).where(Outcome.case_id == "case_order_b")) is None
        case_a = session.get(RecoveryCase, "case_order_a")
        assert case_a is not None
        assert case_a.state == "awaiting_outcome"


@pytest.mark.asyncio
async def test_captured_webhook_reconciles_escalated_case_but_preserves_stopped_case(app):
    with app.state.session_factory() as session:
        session.add_all(
            [
                RecoveryCase(
                    case_id="case_escalated_capture",
                    payment_id="pay_escalated_capture",
                    amount_at_risk=249900,
                    state="escalated",
                    attempts=1,
                ),
                RecoveryCase(
                    case_id="case_stopped_capture",
                    payment_id="pay_stopped_capture",
                    amount_at_risk=249900,
                    state="stopped",
                    attempts=1,
                ),
                PaymentEvent(
                    event_id="evt_escalated_failure",
                    provider_event_id="provider_escalated_failure",
                    event_type="payment.failed",
                    payment_id="pay_escalated_capture",
                    amount=249900,
                    currency="INR",
                    status="failed",
                    occurred_at=datetime(2024, 8, 24, 6, 31, tzinfo=UTC),
                    provider="razorpay_test",
                    raw_hash="escalated-failure-hash",
                ),
                PaymentEvent(
                    event_id="evt_stopped_failure",
                    provider_event_id="provider_stopped_failure",
                    event_type="payment.failed",
                    payment_id="pay_stopped_capture",
                    amount=249900,
                    currency="INR",
                    status="failed",
                    occurred_at=datetime(2024, 8, 24, 6, 31, tzinfo=UTC),
                    provider="razorpay_test",
                    raw_hash="stopped-failure-hash",
                ),
            ]
        )
        session.commit()

    def payload(payment_id: str) -> bytes:
        return json.dumps(
            {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": payment_id,
                            "amount": 249900,
                            "currency": "INR",
                            "status": "captured",
                            "created_at": 1724481100,
                        }
                    }
                },
            },
            separators=(",", ":"),
        ).encode()

    escalated_body = payload("pay_escalated_capture")
    stopped_body = payload("pay_stopped_capture")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        escalated_response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=escalated_body,
            headers={"X-Razorpay-Signature": signature(escalated_body)},
        )
        stopped_response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=stopped_body,
            headers={"X-Razorpay-Signature": signature(stopped_body)},
        )

    assert escalated_response.status_code == 202
    assert stopped_response.status_code == 202
    with app.state.session_factory() as session:
        escalated = session.get(RecoveryCase, "case_escalated_capture")
        stopped = session.get(RecoveryCase, "case_stopped_capture")
        assert escalated is not None
        assert stopped is not None
        assert escalated.state == "recovered"
        assert stopped.state == "stopped"
        assert (
            session.scalar(select(Outcome).where(Outcome.case_id == escalated.case_id)) is not None
        )
        assert session.scalar(select(Outcome).where(Outcome.case_id == stopped.case_id)) is None


@pytest.mark.asyncio
async def test_matching_captured_webhook_records_one_outcome_on_duplicate_delivery(app):
    def body(event: str, created_at: int) -> bytes:
        entity = {
            "id": "pay_duplicate_capture",
            "amount": 249900,
            "currency": "INR",
            "status": "captured" if event == "payment.captured" else "failed",
            "created_at": created_at,
            "order_id": "order_duplicate_capture",
            "notes": {"customer_id": "cust_duplicate_capture"},
        }
        if event == "payment.failed":
            entity.update(
                {
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "insufficient funds",
                }
            )
        return json.dumps(
            {"event": event, "payload": {"payment": {"entity": entity}}},
            separators=(",", ":"),
        ).encode()

    failure = body("payment.failed", 1724481000)
    capture = body("payment.captured", 1724481100)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        failed = await client.post(
            "/api/v1/webhooks/razorpay",
            content=failure,
            headers={"X-Razorpay-Signature": signature(failure)},
        )
        first_capture = await client.post(
            "/api/v1/webhooks/razorpay",
            content=capture,
            headers={"X-Razorpay-Signature": signature(capture)},
        )
        duplicate_capture = await client.post(
            "/api/v1/webhooks/razorpay",
            content=capture,
            headers={"X-Razorpay-Signature": signature(capture)},
        )
        dashboard = await client.get("/api/v1/dashboard")
        audit = await client.get("/api/v1/audit/case_order_duplicate_capture")

    assert failed.status_code == 202
    assert first_capture.status_code == 202
    assert duplicate_capture.status_code == 200
    assert dashboard.json()["executive"]["test_mode_value"] == 249900
    event_types = [event["event_type"] for event in audit.json()]
    assert event_types.count("outcome.recorded") == 1
    assert event_types.count("event.duplicate") == 1
    with app.state.session_factory() as session:
        outcomes = session.scalars(
            select(Outcome).where(Outcome.case_id == "case_order_duplicate_capture")
        ).all()
        case = session.get(RecoveryCase, "case_order_duplicate_capture")
        assert len(outcomes) == 1
        assert outcomes[0].recovered_amount == 249900
        assert case is not None
        assert case.state == "recovered"


@pytest.mark.asyncio
async def test_unreferenced_capture_does_not_attribute_to_an_obligation_case(app):
    with app.state.session_factory() as session:
        session.add_all(
            [
                Customer(customer_id="cust_unreferenced", consent=True),
                RecoveryCase(
                    case_id="case_order_unreferenced",
                    customer_id="cust_unreferenced",
                    payment_id="pay_shared_unreferenced",
                    obligation_reference="order_unreferenced",
                    amount_at_risk=249900,
                    state="awaiting_outcome",
                    attempts=0,
                ),
                PaymentEvent(
                    event_id="evt_failure_unreferenced",
                    provider_event_id="provider_failure_unreferenced",
                    event_type="payment.failed",
                    payment_id="pay_shared_unreferenced",
                    obligation_reference="order_unreferenced",
                    customer_id="cust_unreferenced",
                    amount=249900,
                    currency="INR",
                    status="failed",
                    occurred_at=datetime(2024, 8, 24, 6, 31, tzinfo=UTC),
                    provider="razorpay_test",
                    raw_hash="failure-unreferenced-hash",
                ),
            ]
        )
        session.commit()

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_shared_unreferenced",
                    "amount": 249900,
                    "currency": "INR",
                    "status": "captured",
                    "created_at": 1724481100,
                    "notes": {"customer_id": "cust_unreferenced"},
                }
            }
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": signature(body)},
        )

    assert response.status_code == 202
    with app.state.session_factory() as session:
        case = session.get(RecoveryCase, "case_order_unreferenced")
        assert case is not None
        assert case.state == "awaiting_outcome"
        assert session.scalar(select(Outcome).where(Outcome.case_id == case.case_id)) is None
