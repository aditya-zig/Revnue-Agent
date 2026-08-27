from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.tables import Customer, RecoveryCase
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
                    case_id="case_order_001",
                    customer_id="customer_001",
                    payment_id="payment_001",
                    obligation_reference="order_001",
                    amount_at_risk=249900,
                    state="eligible",
                    attempts=0,
                ),
            ]
        )
        session.commit()
    return app


@pytest.mark.asyncio
async def test_open_exception_blocks_customer_actions_and_no_debit_returns_to_investigation(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/cases/case_order_001/exceptions",
            json={"kind": "customer_debit_claim", "evidence": {"claim": "debited"}},
        )
        policy = await client.get("/api/v1/cases/case_order_001/policy")
        blocked = await client.post(
            "/api/v1/cases/case_order_001/actions",
            json={"action": "payment_link", "idempotency_key": "blocked-link"},
        )
        resolved = await client.post(
            f"/api/v1/exceptions/{created.json()['exception_id']}/resolve",
            json={"resolution": "no_debit", "evidence": {"provider_status": "failed"}},
            headers={"X-Reroute-Role": "business_owner"},
        )
        cases = await client.get("/api/v1/cases")
        audit = await client.get("/api/v1/audit/case_order_001")

    assert created.status_code == 201
    assert policy.json()["blocked_reasons"]["payment_link"] == ["payment_exception"]
    assert policy.json()["blocked_reasons"]["contact"] == ["payment_exception"]
    assert blocked.status_code == 409
    assert resolved.json()["state"] == "resolved"
    assert resolved.json()["resolution"] == "no_debit"
    assert cases.json()[0]["state"] == "investigated"
    event_types = [event["event_type"] for event in audit.json()]
    assert "exception.opened" in event_types
    assert "action.blocked" in event_types
    assert event_types[-2:] == ["case.investigated", "exception.resolved"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolution", "case_state"),
    [("captured", "recovered"), ("refunded", "stopped")],
)
async def test_exception_resolution_sets_terminal_case_state(app, resolution, case_state):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/cases/case_order_001/exceptions",
            json={"kind": "provider_reversal", "evidence": {"provider_status": "pending"}},
        )
        resolved = await client.post(
            f"/api/v1/exceptions/{created.json()['exception_id']}/resolve",
            json={"resolution": resolution, "evidence": {"reference": "proof_001"}},
            headers={"X-Reroute-Role": "business_owner"},
        )
        cases = await client.get("/api/v1/cases")

    assert resolved.status_code == 200
    assert cases.json()[0]["state"] == case_state


@pytest.mark.asyncio
async def test_terminal_case_cannot_open_or_resolve_payment_exception(app):
    with app.state.session_factory() as session:
        case = session.get(RecoveryCase, "case_order_001")
        assert case is not None
        case.state = "recovered"
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/cases/case_order_001/exceptions",
            json={"kind": "customer_debit_claim", "evidence": {"claim": "debited"}},
        )

    assert response.status_code == 409
