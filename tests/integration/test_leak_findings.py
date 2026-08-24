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
        detected = await client.post("/api/v1/findings/detect")
        response = await client.get("/api/v1/findings")
        detail = await client.get(f"/api/v1/findings/{response.json()[0]['finding_id']}")

    assert imported.status_code == 201
    assert detected.status_code == 200
    assert response.status_code == 200
    findings = response.json()
    assert {finding["finding_id"] for finding in detected.json()} == {
        finding["finding_id"] for finding in findings
    }
    assert detail.status_code == 200
    assert detail.json() == findings[0]
    error_reason_finding = next(
        finding
        for finding in findings
        if finding["cohort_filter"]
        == {"dimension": "error_reason", "value": "insufficient funds"}
    )
    assert error_reason_finding["cohort_filter"] == {
        "dimension": "error_reason",
        "value": "insufficient funds",
    }
    assert error_reason_finding["impact"] == 112500
    assert error_reason_finding["recoverable_impact"] == 56250
    assert error_reason_finding["evidence"] == {
        "attempted_value": 300000,
        "data_quality_warnings": [],
        "event_ids": ["evt_001", "evt_002", "evt_003"],
        "failure_count": 3,
        "failed_value": 300000,
        "recovery_probability": 0.5,
        "support": 3,
        "unresolved_value": 300000,
    }
    assert {
        finding["cohort_filter"]["dimension"] for finding in findings
    } == {
        "amount_bucket",
        "customer_history",
        "day_bucket",
        "error_code",
        "error_reason",
        "method",
        "normalized_error_reason",
        "prior_successful_payments",
        "hour_bucket",
        "failure_sequence",
    }
    assert all(finding["evidence"]["support"] >= 3 for finding in findings)
    assert all(
        finding["cohort_filter"]["value"] != "issuer unavailable" for finding in findings
    )


@pytest.mark.asyncio
async def test_detector_groups_error_source_step_code_and_normalized_reason(app):
    header = [
        "event_id",
        "event_type",
        "payment_id",
        "customer_id",
        "amount",
        "currency",
        "method",
        "status",
        "error_source",
        "error_step",
        "error_code",
        "error_reason",
        "occurred_at",
    ]
    failures = [
        ["evt_001", "pay_001", "Insufficient funds", "2026-08-24T04:00:00+00:00"],
        ["evt_002", "pay_002", " insufficient   funds ", "2026-08-24T04:05:00+00:00"],
        ["evt_003", "pay_003", "INSUFFICIENT FUNDS", "2026-08-24T04:10:00+00:00"],
    ]
    rows = [
        [
            event_id,
            "payment.failed",
            payment_id,
            f"cust_{event_id}",
            "100000",
            "INR",
            "upi",
            "failed",
            "gateway",
            "authorization",
            "BAD_REQUEST_ERROR",
            error_reason,
            occurred_at,
        ]
        for event_id, payment_id, error_reason, occurred_at in failures
    ]
    rows.extend(
        [
            [
                f"evt_00{index}",
                "payment.captured",
                f"pay_00{index}",
                f"cust_00{index}",
                "10000",
                "INR",
                "card",
                "captured",
                "",
                "",
                "",
                "",
                f"2026-08-23T12:0{index}:00+00:00",
            ]
            for index in range(4, 7)
        ]
    )
    csv = "\n".join(",".join(row) for row in [header, *rows])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        imported = await client.post("/api/v1/data/import", content=csv)
        findings = await client.post("/api/v1/findings/detect")

    assert imported.status_code == 201
    assert findings.status_code == 200
    cohort_filters = {tuple(finding["cohort_filter"].items()) for finding in findings.json()}
    assert (("dimension", "error_source"), ("value", "gateway")) in cohort_filters
    assert (("dimension", "error_step"), ("value", "authorization")) in cohort_filters
    assert (("dimension", "error_code"), ("value", "BAD_REQUEST_ERROR")) in cohort_filters
    assert (
        ("dimension", "normalized_error_reason"),
        ("value", "insufficient_funds"),
    ) in cohort_filters


@pytest.mark.asyncio
async def test_detector_groups_repeated_payment_failures(app):
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
        "prior_failures",
    ]
    rows = [
        [
            f"evt_{payment_id}_{failure_number}",
            "payment.failed",
            f"pay_{payment_id}_{failure_number}",
            f"cust_{payment_id}",
            "100000",
            "INR",
            "upi",
            "failed",
            "BAD_REQUEST_ERROR",
            "insufficient funds",
            f"2026-08-24T04:0{failure_number}:00+00:00",
            "0",
        ]
        for payment_id in range(1, 4)
        for failure_number in range(1, 3)
    ]
    rows.extend(
        [
            [
                f"evt_capture_{payment_id}",
                "payment.captured",
                f"pay_capture_{payment_id}",
                f"cust_capture_{payment_id}",
                "10000",
                "INR",
                "card",
                "captured",
                "",
                "",
                f"2026-08-23T12:0{payment_id}:00+00:00",
                "0",
            ]
            for payment_id in range(1, 4)
        ]
    )
    csv = "\n".join(",".join(row) for row in [header, *rows])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/data/import", content=csv)
        findings = await client.post("/api/v1/findings/detect")

    assert {
        finding["cohort_filter"]["value"]
        for finding in findings.json()
        if finding["cohort_filter"]["dimension"] == "failure_sequence"
    } == {"first_failure", "repeated_failure"}
