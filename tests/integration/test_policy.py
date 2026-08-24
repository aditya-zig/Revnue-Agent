from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.tables import ActionEvent, Customer, PaymentEvent, RecoveryCase
from app.main import create_app

NOW = datetime(2026, 8, 24, 10, tzinfo=UTC)


@pytest.fixture
def app(database_url):
    return create_app(database_url=database_url, policy_now=lambda: NOW)


@pytest.mark.asyncio
async def test_policy_allows_recovery_actions_for_an_eligible_case(app):
    csv = "\n".join(
        [
            "event_id,event_type,payment_id,customer_id,amount,currency,method,status,error_code,error_reason,occurred_at,consent",
            "evt_001,payment.failed,pay_001,cust_001,249900,INR,upi,failed,"
            "BAD_REQUEST_ERROR,insufficient funds,2026-08-24T04:00:00+00:00,true",
        ]
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        imported = await client.post("/api/v1/data/import", content=csv)
        response = await client.get("/api/v1/cases/case_pay_001/policy")

    assert imported.status_code == 201
    assert response.status_code == 200
    assert response.json() == {
        "allowed_actions": ["contact", "retry", "escalate"],
        "blocked_reasons": {},
        "policy_version": "v1",
    }


@pytest.mark.asyncio
async def test_policy_blocks_hard_decline_retries_and_contact_without_consent_or_identity(app):
    with app.state.session_factory() as session:
        session.add_all(
            [
                Customer(customer_id="cust_hard", consent=True),
                Customer(customer_id="cust_no_consent", consent=False),
                RecoveryCase(
                    case_id="case_hard",
                    customer_id="cust_hard",
                    payment_id="pay_hard",
                    amount_at_risk=249900,
                    state="eligible",
                    attempts=0,
                ),
                RecoveryCase(
                    case_id="case_no_consent",
                    customer_id="cust_no_consent",
                    payment_id="pay_no_consent",
                    amount_at_risk=249900,
                    state="eligible",
                    attempts=0,
                ),
                RecoveryCase(
                    case_id="case_no_identity",
                    customer_id=None,
                    payment_id="pay_no_identity",
                    amount_at_risk=249900,
                    state="eligible",
                    attempts=0,
                ),
                PaymentEvent(
                    event_id="evt_hard",
                    provider_event_id="provider_evt_hard",
                    event_type="payment.failed",
                    payment_id="pay_hard",
                    customer_id="cust_hard",
                    amount=249900,
                    currency="INR",
                    method="card",
                    status="failed",
                    error_code="hard_decline",
                    occurred_at=NOW,
                    provider="test",
                    raw_hash="hard",
                ),
            ]
        )
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hard_decline = await client.get("/api/v1/cases/case_hard/policy")
        no_consent = await client.get("/api/v1/cases/case_no_consent/policy")
        no_identity = await client.get("/api/v1/cases/case_no_identity/policy")

    assert hard_decline.json()["blocked_reasons"] == {"retry": ["hard_decline"]}
    assert no_consent.json()["blocked_reasons"] == {"contact": ["missing_consent"]}
    assert no_identity.json()["blocked_reasons"] == {"contact": ["missing_identity"]}


@pytest.mark.asyncio
async def test_policy_enforces_contact_and_daily_action_limits(app):
    with app.state.session_factory() as session:
        session.add_all(
            [
                Customer(customer_id="cust_001", consent=True),
                RecoveryCase(
                    case_id="case_limits",
                    customer_id="cust_001",
                    payment_id="pay_limits",
                    amount_at_risk=249900,
                    state="eligible",
                    attempts=0,
                ),
                *[
                    ActionEvent(
                        action_id=f"contact_{index}",
                        case_id="case_limits",
                        idempotency_key=f"contact_{index}",
                        tool="contact",
                        input_hash=f"contact_{index}",
                        status="completed",
                        executed_at=NOW - timedelta(days=index + 2),
                    )
                    for index in range(3)
                ],
            ]
        )
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        contact_limited = await client.get("/api/v1/cases/case_limits/policy")

    assert contact_limited.json()["blocked_reasons"] == {"contact": ["contact_limit"]}

    with app.state.session_factory() as session:
        session.add(
            ActionEvent(
                action_id="recent_action",
                case_id="case_limits",
                idempotency_key="recent_action",
                tool="retry",
                input_hash="recent_action",
                status="completed",
                executed_at=NOW - timedelta(hours=23),
            )
        )
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        action_limited = await client.get("/api/v1/cases/case_limits/policy")

    assert action_limited.json()["blocked_reasons"] == {
        "contact": ["contact_limit", "action_limit"],
        "retry": ["action_limit"],
        "escalate": ["action_limit"],
    }


@pytest.mark.asyncio
async def test_policy_blocks_contact_during_kolkata_quiet_hours(database_url):
    quiet_app = create_app(
        database_url=database_url,
        policy_now=lambda: datetime(2026, 8, 24, 17, tzinfo=UTC),
    )
    with quiet_app.state.session_factory() as session:
        session.add_all(
            [
                Customer(customer_id="cust_001", consent=True),
                RecoveryCase(
                    case_id="case_quiet",
                    customer_id="cust_001",
                    payment_id="pay_quiet",
                    amount_at_risk=249900,
                    state="eligible",
                    attempts=0,
                ),
            ]
        )
        session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=quiet_app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/cases/case_quiet/policy")

    assert response.json()["blocked_reasons"] == {"contact": ["quiet_hours"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    ["paid", "refunded", "disputed", "opted_out", "closed", "recovered", "stopped", "escalated"],
)
async def test_policy_blocks_every_action_for_terminal_cases(app, state):
    with app.state.session_factory() as session:
        session.add(
            RecoveryCase(
                case_id=f"case_{state}",
                customer_id="cust_001",
                payment_id=f"pay_{state}",
                amount_at_risk=249900,
                state=state,
                attempts=0,
            )
        )
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/cases/case_{state}/policy")

    assert response.status_code == 200
    assert response.json() == {
        "allowed_actions": [],
        "blocked_reasons": {
            "contact": ["terminal_case"],
            "retry": ["terminal_case"],
            "escalate": ["terminal_case"],
        },
        "policy_version": "v1",
    }
