import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient

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
        }
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
