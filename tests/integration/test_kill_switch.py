from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.tables import Customer, RecoveryCase
from app.main import create_app

NOW = datetime(2026, 8, 24, 10, tzinfo=UTC)


@pytest.mark.asyncio
async def test_kill_switch_blocks_all_actions(database_url):
    app = create_app(database_url=database_url, policy_now=lambda: NOW, kill_switch=True)
    with app.state.session_factory() as session:
        session.add_all(
            [
                Customer(customer_id="cust_001", consent=True),
                RecoveryCase(
                    case_id="case_001",
                    customer_id="cust_001",
                    payment_id="pay_001",
                    amount_at_risk=249900,
                    state="eligible",
                    attempts=0,
                ),
            ]
        )
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.get("/api/v1/cases/case_001/policy")
        action = await client.post(
            "/api/v1/cases/case_001/actions",
            json={"action": "contact", "idempotency_key": "kill-001"},
        )

    assert policy.json()["allowed_actions"] == []
    assert all(reason == ["kill_switch"] for reason in policy.json()["blocked_reasons"].values())
    assert action.status_code == 409
