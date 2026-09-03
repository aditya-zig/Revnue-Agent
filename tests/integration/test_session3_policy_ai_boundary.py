import json
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.tables import Customer, PaymentEvent, RecoveryCase
from app.main import create_app

NOW = datetime(2026, 8, 24, 10, tzinfo=UTC)


def _seed_hard_decline(app, *, case_id: str = "case_hard", payment_id: str = "pay_hard") -> None:
    with app.state.session_factory() as session:
        session.add_all(
            [
                Customer(customer_id=f"cust_{case_id}", consent=True),
                RecoveryCase(
                    case_id=case_id,
                    customer_id=f"cust_{case_id}",
                    payment_id=payment_id,
                    amount_at_risk=249900,
                    state="eligible",
                    attempts=0,
                ),
                PaymentEvent(
                    event_id=f"evt_{case_id}",
                    provider_event_id=f"provider_evt_{case_id}",
                    event_type="payment.failed",
                    payment_id=payment_id,
                    customer_id=f"cust_{case_id}",
                    amount=249900,
                    currency="INR",
                    method="card",
                    status="failed",
                    error_code="hard_decline",
                    error_reason="issuer declined payment",
                    occurred_at=NOW,
                    provider="razorpay_test",
                    raw_hash=f"hash_{case_id}",
                ),
            ]
        )
        session.commit()


@pytest.mark.asyncio
async def test_hard_decline_retry_is_removed_before_model_receives_ranking_input(database_url):
    model_inputs: list[dict] = []

    def decide(payload: dict) -> dict:
        model_inputs.append(payload)
        return {"selected_action": "escalate"}

    app = create_app(
        database_url=database_url,
        policy_now=lambda: NOW,
        decide_recovery_action=decide,
    )
    _seed_hard_decline(app)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/cases/case_hard/decisions",
            json={"idempotency_key": "hard-decline-ranking-proof"},
        )
        audit = await client.get("/api/v1/audit/case_hard")

    assert response.status_code == 201
    assert len(model_inputs) == 1
    ranking_input = model_inputs[0]
    assert set(ranking_input) == {
        "input_version",
        "policy_version",
        "model_version",
        "candidate_actions",
    }
    assert {candidate["action"] for candidate in ranking_input["candidate_actions"]} == {
        "payment_link",
        "contact",
        "promise",
        "escalate",
    }
    assert "retry" not in json.dumps(ranking_input, sort_keys=True).lower()

    evidence = response.json()["evidence"]
    assert evidence["ai_ranking_input"] == ranking_input
    assert {
        "action": "retry",
        "reasons": ["hard_decline"],
        "status": "removed_before_ai_ranking",
    } in evidence["blocked_before_ai_ranking"]
    policy_event = next(
        event for event in audit.json() if event["event_type"] == "policy.evaluated_before_ai_ranking"
    )
    assert policy_event["payload"]["allowed_actions"] == [
        "payment_link",
        "contact",
        "promise",
        "escalate",
    ]
    assert {
        "action": "retry",
        "reasons": ["hard_decline"],
        "status": "removed_before_ai_ranking",
    } in policy_event["payload"]["blocked_actions"]


@pytest.mark.asyncio
async def test_model_cannot_restore_action_removed_by_policy(database_url):
    app = create_app(
        database_url=database_url,
        policy_now=lambda: NOW,
        decide_recovery_action=lambda payload: {"selected_action": "retry"},
    )
    _seed_hard_decline(app)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/cases/case_hard/decisions",
            json={"idempotency_key": "model-tries-blocked-retry"},
        )

    assert response.status_code == 201
    assert response.json()["selected_action"] != "retry"
    assert response.json()["selection_source"] == "fallback"
    assert response.json()["action"] is None


@pytest.mark.asyncio
async def test_kill_switch_stops_before_model_ranking(database_url):
    calls = 0

    def decide(payload: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"selected_action": "escalate"}

    app = create_app(
        database_url=database_url,
        policy_now=lambda: NOW,
        decide_recovery_action=decide,
        kill_switch=True,
    )
    with app.state.session_factory() as session:
        session.add_all(
            [
                Customer(customer_id="cust_kill", consent=True),
                RecoveryCase(
                    case_id="case_kill",
                    customer_id="cust_kill",
                    payment_id="pay_kill",
                    amount_at_risk=249900,
                    state="eligible",
                    attempts=0,
                ),
            ]
        )
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/cases/case_kill/decisions",
            json={"idempotency_key": "kill-switch-proof"},
        )
        audit = await client.get("/api/v1/audit/case_kill")

    assert response.status_code == 409
    assert response.json() == {"detail": ["no_allowed_action"]}
    assert calls == 0
    policy_event = next(
        event for event in audit.json() if event["event_type"] == "policy.evaluated_before_ai_ranking"
    )
    assert policy_event["payload"]["allowed_actions"] == []
    assert {entry["action"] for entry in policy_event["payload"]["blocked_actions"]} == {
        "payment_link",
        "contact",
        "retry",
        "promise",
        "escalate",
    }


@pytest.mark.asyncio
async def test_provider_failure_never_creates_recovered_outcome(database_url):
    def fail_payment_link(amount: int, idempotency_key: str) -> str:
        raise ValueError("provider response contained secret-token-that-must-not-leak")

    app = create_app(
        database_url=database_url,
        policy_now=lambda: NOW,
        create_payment_link=fail_payment_link,
        decide_recovery_action=lambda payload: {"selected_action": "payment_link"},
    )
    with app.state.session_factory() as session:
        session.add_all(
            [
                Customer(customer_id="cust_provider_failure", consent=True),
                RecoveryCase(
                    case_id="case_provider_failure",
                    customer_id="cust_provider_failure",
                    payment_id="pay_provider_failure",
                    amount_at_risk=249900,
                    state="eligible",
                    attempts=0,
                ),
            ]
        )
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        failed = await client.post(
            "/api/v1/cases/case_provider_failure/decisions",
            json={"idempotency_key": "provider-failure-no-outcome", "approved": True},
            headers={"X-Reroute-Role": "business_owner"},
        )
        outcome = await client.get("/api/v1/cases/case_provider_failure/outcome")
        audit = await client.get("/api/v1/audit/case_provider_failure")

    assert failed.status_code == 502
    assert outcome.status_code == 200
    assert outcome.json()["outcome"] is None
    audit_text = json.dumps(audit.json(), sort_keys=True)
    assert "secret-token-that-must-not-leak" not in audit_text
    assert any(event["event_type"] == "action.failed" for event in audit.json())
    assert any(event["event_type"] == "case.escalated" for event in audit.json())
    assert not any(event["event_type"] == "outcome.recorded" for event in audit.json())
