import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def app(database_url):
    return create_app(database_url=database_url)


@pytest.mark.asyncio
async def test_detector_persists_and_returns_ranked_supported_leak_evidence(app):
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
        "successful_payments",
    ]
    rows = [
        [
            "evt_001",
            "payment.failed",
            "pay_001",
            "cust_001",
            "100000",
            "INR",
            "upi",
            "failed",
            "BAD_REQUEST_ERROR",
            "insufficient funds",
            "2026-08-24T04:00:00+00:00",
            "4",
        ],
        [
            "evt_002",
            "payment.failed",
            "pay_002",
            "cust_002",
            "100000",
            "INR",
            "upi",
            "failed",
            "BAD_REQUEST_ERROR",
            "insufficient funds",
            "2026-08-24T04:05:00+00:00",
            "4",
        ],
        [
            "evt_003",
            "payment.failed",
            "pay_003",
            "cust_003",
            "100000",
            "INR",
            "upi",
            "failed",
            "BAD_REQUEST_ERROR",
            "insufficient funds",
            "2026-08-24T04:10:00+00:00",
            "4",
        ],
        [
            "evt_004",
            "payment.captured",
            "pay_004",
            "cust_004",
            "10000",
            "INR",
            "card",
            "captured",
            "",
            "",
            "2026-08-23T12:00:00+00:00",
            "0",
        ],
        [
            "evt_005",
            "payment.captured",
            "pay_005",
            "cust_005",
            "10000",
            "INR",
            "card",
            "captured",
            "",
            "",
            "2026-08-23T12:05:00+00:00",
            "0",
        ],
        [
            "evt_006",
            "payment.captured",
            "pay_006",
            "cust_006",
            "10000",
            "INR",
            "card",
            "captured",
            "",
            "",
            "2026-08-23T12:10:00+00:00",
            "0",
        ],
        [
            "evt_007",
            "payment.failed",
            "pay_007",
            "cust_007",
            "50000",
            "INR",
            "card",
            "failed",
            "NETWORK_ERROR",
            "issuer unavailable",
            "2026-08-25T06:00:00+00:00",
            "0",
        ],
        [
            "evt_008",
            "payment.failed",
            "pay_008",
            "cust_008",
            "50000",
            "INR",
            "card",
            "failed",
            "NETWORK_ERROR",
            "issuer unavailable",
            "2026-08-25T06:05:00+00:00",
            "0",
        ],
    ]
    csv = "\n".join(",".join(row) for row in [header, *rows])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        imported = await client.post("/api/v1/data/import", content=csv)
        response = await client.get("/api/v1/findings")

    assert imported.status_code == 201
    assert response.status_code == 200
    findings = response.json()
    assert findings[0]["cohort_filter"] == {
        "dimension": "error_reason",
        "value": "insufficient funds",
    }
    assert findings[0]["impact"] == 112500
    assert findings[0]["recoverable_impact"] == 112500
    assert findings[0]["evidence"] == {
        "attempted_value": 300000,
        "data_quality_warnings": [],
        "event_ids": ["evt_001", "evt_002", "evt_003"],
        "failure_count": 3,
        "failed_value": 300000,
        "recovery_probability": 1.0,
        "support": 3,
        "unresolved_value": 300000,
    }
    assert {
        finding["cohort_filter"]["dimension"] for finding in findings
    } == {
        "amount_bucket",
        "customer_history",
        "day_bucket",
        "error_reason",
        "method",
        "prior_successful_payments",
        "time_bucket",
    }
    assert all(finding["evidence"]["support"] >= 3 for finding in findings)
    assert all(
        finding["cohort_filter"]["value"] != "issuer unavailable" for finding in findings
    )
