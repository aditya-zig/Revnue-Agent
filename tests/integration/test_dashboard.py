import pytest
from httpx import ASGITransport, AsyncClient

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
    assert case["policy"]["policy_version"]
    assert case["human_review"]["allowed_actions"]
    assert payload["timeline"][0]["events"][0]["kind"] == "raw event"
    payment_exception = payload["payment_exceptions"][0]
    assert payment_exception["resolution"] == "no_debit"
    assert payment_exception["resolution_evidence"] == {"bank_reference": "ref_001"}
