from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.tables import PaymentEvent, RecoveryCase
from app.main import create_app


@pytest.fixture
def app(database_url):
    return create_app(database_url=database_url)


@pytest.mark.asyncio
async def test_dashboard_exposes_recovery_work_at_http_seam(app):
    csv = "\n".join(
        [
            (
                "event_id,event_type,payment_id,customer_id,amount,currency,method,status,"
                "error_code,error_reason,occurred_at,tenure_days,successful_payments,"
                "prior_failures,preferred_method,consent,locale"
            ),
            (
                "evt_001,payment.failed,pay_001,cust_001,249900,INR,upi,failed,"
                "BAD_REQUEST_ERROR,insufficient funds,2026-08-24T04:00:00+00:00,120,6,1,"
                "upi,true,en-IN"
            ),
        ]
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/data/import", content=csv)
        cases = (await client.get("/api/v1/cases")).json()
        case_id = cases[0]["case_id"]
        exception = await client.post(
            f"/api/v1/cases/{case_id}/exceptions",
            json={"kind": "customer_debit_claim", "evidence": {"claim": "debit"}},
        )
        exception_id = exception.json()["exception_id"]
        await client.post(
            f"/api/v1/exceptions/{exception_id}/resolve",
            headers={"X-Reroute-Role": "business_owner"},
            json={"resolution": "no_debit", "evidence": {"bank_reference": "ref_001"}},
        )
        page = await client.get("/")
        response = await client.get("/api/v1/dashboard")

    assert page.status_code == 200
    assert response.status_code == 200
    assert "ReRoute Sentinel" in page.text
    assert 'data-view="home"' in page.text
    assert 'data-view="payments"' in page.text
    assert 'data-view="incidents"' in page.text
    assert 'data-view="recoveries"' in page.text
    assert 'data-view="policy"' in page.text
    assert 'data-view="outcomes"' in page.text
    assert "Simulate 999 Payments" not in page.text
    assert "Refresh data" not in page.text
    assert "/static/js/sentinel-console.js" in page.text

    payload = response.json()
    case = payload["worklist"][0]
    assert {
        "executive",
        "investigation",
        "worklist",
        "timeline",
        "evaluation",
        "mock_inbox",
        "payment_exceptions",
        "policy_settings",
    } <= payload.keys()
    assert case["evidence"]["event_id"] == "evt_001"
    assert case["evidence"]["provider"] == "csv_import"
    assert payload["executive"]["revenue_at_risk_claim_tag"] == ""
    assert case["policy"]["policy_version"]
    assert case["human_review"]["allowed_actions"]
    assert payload["timeline"][0]["events"][0]["kind"] == "raw event"
    payment_exception = payload["payment_exceptions"][0]
    assert payment_exception["resolution"] == "no_debit"
    assert payment_exception["resolution_evidence"] == {"bank_reference": "ref_001"}


@pytest.mark.asyncio
async def test_dashboard_suppresses_claims_for_missing_and_unknown_payment_evidence(app):
    with app.state.session_factory() as session:
        session.add_all(
            [
                RecoveryCase(
                    case_id="case_missing_evidence",
                    payment_id="pay_missing_evidence",
                    amount_at_risk=10000,
                    state="detected",
                    attempts=0,
                ),
                RecoveryCase(
                    case_id="case_unknown_evidence",
                    payment_id="pay_unknown_evidence",
                    amount_at_risk=20000,
                    state="detected",
                    attempts=0,
                ),
                PaymentEvent(
                    event_id="evt_unknown_evidence",
                    provider_event_id="provider_unknown_evidence",
                    event_type="payment.failed",
                    payment_id="pay_unknown_evidence",
                    amount=20000,
                    currency="INR",
                    status="failed",
                    occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                    provider="unknown",
                    raw_hash="unknown-evidence-hash",
                ),
            ]
        )
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard")

    assert response.status_code == 200
    payload = response.json()
    worklist = {item["case_id"]: item for item in payload["worklist"]}
    assert worklist["case_missing_evidence"]["evidence"] is None
    assert worklist["case_missing_evidence"]["evidence_providers"] == [None]
    assert worklist["case_unknown_evidence"]["evidence"]["provider"] == "unknown"
    assert worklist["case_unknown_evidence"]["evidence_providers"] == ["unknown"]
    assert payload["executive"]["revenue_at_risk_claim_tag"] == ""
