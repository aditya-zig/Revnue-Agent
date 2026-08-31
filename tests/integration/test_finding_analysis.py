import pytest
from httpx import ASGITransport, AsyncClient

from app.db.tables import FindingAnalysis, RecoveryCase
from app.main import create_app


@pytest.fixture
def app(database_url):
    return create_app(database_url=database_url)


def finding_csv() -> str:
    header = (
        "event_id,event_type,payment_id,customer_id,amount,currency,method,status,"
        "error_code,error_reason,occurred_at,consent"
    )
    rows = [
        f"evt_{number},payment.failed,pay_{number},cust_{number},125000,INR,upi,failed,"
        f"BAD_REQUEST_ERROR,insufficient funds,2026-08-24T04:0{number}:00+00:00,true"
        for number in range(1, 4)
    ]
    rows.extend(
        f"captured_{number},payment.captured,captured_pay_{number},captured_cust_{number},"
        f"125000,INR,card,captured,,,2026-08-24T05:0{number}:00+00:00,true"
        for number in range(1, 4)
    )
    return "\n".join([header, *rows])


async def detect_one(client: AsyncClient) -> dict:
    await client.post("/api/v1/data/import", content=finding_csv())
    response = await client.post("/api/v1/findings/detect")
    assert response.status_code == 200
    return next(
        finding
        for finding in response.json()
        if finding["cohort_filter"]
        == {"dimension": "error_reason", "value": "insufficient funds"}
    )


@pytest.mark.asyncio
async def test_analysis_is_explicit_sanitized_idempotent_and_survives_detector_replacement(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        finding = await detect_one(client)
        finding_id = finding["finding_id"]

        assert (await client.get(f"/api/v1/findings/{finding_id}/analysis")).status_code == 404
        created = await client.post(
            f"/api/v1/findings/{finding_id}/analysis",
            json={"idempotency_key": "explain-001"},
        )
        repeated = await client.post(
            f"/api/v1/findings/{finding_id}/analysis",
            json={"idempotency_key": "explain-001"},
        )
        second = await client.post(
            f"/api/v1/findings/{finding_id}/analysis",
            json={"idempotency_key": "explain-002"},
        )

        assert created.status_code == 201
        assert repeated.status_code == 200
        assert repeated.json() == created.json()
        assert second.status_code == 201
        assert second.json()["analysis_id"] != created.json()["analysis_id"]

        saved = created.json()
        assert saved["impact_paise"] == 187500
        assert saved["recoverable_impact_paise"] == 93750
        assert saved["claim_tag"] == "ESTIMATED"
        assert saved["snapshot"]["attempted_value_paise"] == 375000
        assert saved["snapshot"]["failed_value_paise"] == 375000
        assert saved["result"]["external_model_generated"] is False
        assert saved["result"]["model_statement"] == "No external model generated this analysis."
        assert saved["result"]["observed_facts"]
        assert saved["result"]["hypotheses"]

        forbidden = {"raw_body", "customer_id", "event_id", "event_ids", "evidence"}

        def keys(value):
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value))
            return set()

        assert not forbidden & keys(saved["snapshot"])
        assert not forbidden & keys(saved["result"])

        old_analysis = await client.get(f"/api/v1/findings/{finding_id}/analysis")
        assert old_analysis.status_code == 200
        assert old_analysis.json()["analysis_id"] == second.json()["analysis_id"]
        by_id = await client.get(f"/api/v1/finding-analyses/{created.json()['analysis_id']}")
        assert by_id.status_code == 200
        dashboard = await client.get("/api/v1/dashboard")
        assert (
            dashboard.json()["investigation"]["analysis"]["analysis_id"]
            == second.json()["analysis_id"]
        )

        replacement = await client.post("/api/v1/findings/detect")
        assert replacement.status_code == 200
        assert all(item["finding_id"] != finding_id for item in replacement.json())
        assert (await client.get(f"/api/v1/findings/{finding_id}/analysis")).status_code == 200


@pytest.mark.asyncio
async def test_analysis_does_not_change_policy_or_action_execution(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        finding = await detect_one(client)
        analysis = await client.post(
            f"/api/v1/findings/{finding['finding_id']}/analysis",
            json={"idempotency_key": "policy-action-analysis"},
        )
        assert analysis.status_code == 201

        with app.state.session_factory() as session:
            session.get(RecoveryCase, "case_pay_1").state = "eligible"
            session.commit()
        policy = await client.get("/api/v1/cases/case_pay_1/policy")
        action = await client.post(
            "/api/v1/cases/case_pay_1/actions",
            json={"action": "contact", "idempotency_key": "analysis-action"},
        )

    assert policy.status_code == 200
    assert "contact" in policy.json()["allowed_actions"]
    assert action.status_code == 201
    assert action.json()["action"] == "contact"
    with app.state.session_factory() as session:
        assert session.query(FindingAnalysis).count() == 1
