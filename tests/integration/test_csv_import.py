import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def app(database_url):
    return create_app(database_url=database_url)


@pytest.mark.asyncio
async def test_csv_import_normalizes_events_and_records_case_transitions(app):
    header = [
        "event_id",
        "event_type",
        "payment_id",
        "customer_id",
        "amount",
        "currency",
        "method",
        "status",
        "error_code",
        "error_reason",
        "occurred_at",
        "tenure_days",
        "successful_payments",
        "prior_failures",
        "preferred_method",
        "consent",
        "locale",
    ]
    failed = [
        "evt_001",
        "payment.failed",
        "pay_001",
        "cust_001",
        "249900",
        "INR",
        "upi",
        "failed",
        "BAD_REQUEST_ERROR",
        "insufficient funds",
        "2026-08-24T04:00:00+00:00",
        "120",
        "6",
        "1",
        "upi",
        "true",
        "en-IN",
    ]
    captured = [
        "evt_002",
        "payment.captured",
        "pay_001",
        "cust_001",
        "249900",
        "INR",
        "upi",
        "captured",
        "",
        "",
        "2026-08-24T05:00:00+00:00",
        "120",
        "6",
        "1",
        "upi",
        "true",
        "en-IN",
    ]
    subscription = [
        "evt_003",
        "subscription.charged",
        "pay_002",
        "cust_002",
        "49900",
        "INR",
        "card",
        "captured",
        "",
        "",
        "2026-08-24T05:00:00+00:00",
        "30",
        "1",
        "0",
        "card",
        "true",
        "hi-IN",
    ]
    csv = "\n".join(",".join(row) for row in [header, failed, captured, subscription, failed])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/data/import",
            content=csv,
            headers={"Content-Type": "text/csv"},
        )
        cases = await client.get("/api/v1/cases")
        audit = await client.get("/api/v1/audit/case_pay_001")

    assert response.status_code == 201
    assert response.json() == {"imported": 3, "duplicates": 1}
    assert cases.json()[0]["state"] == "recovered"
    assert audit.json() == [
        {
            "case_id": "case_pay_001",
            "event_type": "case.detected",
            "payload": {"payment_id": "pay_001"},
        },
        {
            "case_id": "case_pay_001",
            "event_type": "event.recorded",
            "payload": {"event_id": "evt_001", "event_type": "payment.failed"},
        },
        {
            "case_id": "case_pay_001",
            "event_type": "case.recovered",
            "payload": {
                "from": "detected",
                "to": "recovered",
                "payment_id": "pay_001",
            },
        },
        {
            "case_id": "case_pay_001",
            "event_type": "event.recorded",
            "payload": {"event_id": "evt_002", "event_type": "payment.captured"},
        },
    ]
