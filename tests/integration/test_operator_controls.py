from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.tables import Customer, Decision, RecoveryCase
from app.main import create_app

NOW = datetime(2026, 8, 24, 10, tzinfo=UTC)


@pytest.fixture
def app(database_url):
    app = create_app(database_url=database_url, policy_now=lambda: NOW)
    with app.state.session_factory() as session:
        session.add_all(
            [
                Customer(customer_id="customer_001", consent=True),
                RecoveryCase(
                    case_id="case_001",
                    customer_id="customer_001",
                    payment_id="payment_001",
                    amount_at_risk=249900,
                    state="eligible",
                    attempts=0,
                ),
                RecoveryCase(
                    case_id="case_002",
                    customer_id="customer_001",
                    payment_id="payment_002",
                    amount_at_risk=99900,
                    state="investigated",
                    attempts=0,
                ),
                Decision(
                    decision_id="approval_case_001",
                    case_id="case_001",
                    policy_version="v1",
                    model_version="v1",
                    allowed_actions=["contact"],
                    selected_action="contact",
                    expected_value=1,
                    reason_json={"approval": {"required": True, "granted": True}},
                ),
            ]
        )
        session.commit()
    return app


@pytest.mark.asyncio
async def test_only_owner_can_change_versioned_policy_settings(app):
    payload = {
        "quiet_hours_start": 20,
        "quiet_hours_end": 7,
        "contact_limit": 0,
        "kill_switch": False,
        "mock_identity": "ReRoute demo",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.put("/api/v1/policy-settings", json=payload)
        updated = await client.put(
            "/api/v1/policy-settings",
            json=payload,
            headers={"X-Reroute-Role": "business_owner"},
        )
        policy = await client.get("/api/v1/cases/case_001/policy")
        dashboard = await client.get("/api/v1/dashboard")

    assert denied.status_code == 403
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert policy.json()["policy_version"] == "v2"
    assert policy.json()["blocked_reasons"]["contact"] == ["contact_limit"]
    assert dashboard.json()["policy_settings"]["mock_identity"] == "ReRoute demo"


@pytest.mark.asyncio
async def test_mock_opt_out_stops_every_open_case_for_the_customer(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        sent = await client.post(
            "/api/v1/cases/case_001/actions",
            json={"action": "contact", "idempotency_key": "contact-001"},
        )
        replied = await client.post(
            f"/api/v1/mock-inbox/{sent.json()['provider_reference']}/reply",
            json={"reply": "opt_out"},
        )
        cases = await client.get("/api/v1/cases")
        dashboard = await client.get("/api/v1/dashboard")

    assert sent.status_code == 201
    assert replied.status_code == 200
    assert {case["state"] for case in cases.json()} == {"stopped"}
    assert dashboard.json()["mock_inbox"][0]["reply"] == "opt_out"
