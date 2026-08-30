import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.tables import Customer, Decision, RecoveryCase
from app.main import create_app

NOW = datetime(2026, 8, 24, 10, tzinfo=UTC)


@pytest.fixture
def app(database_url):
    created_links: list[dict[str, int | str]] = []

    def create_payment_link(amount: int, idempotency_key: str) -> str:
        created_links.append({"amount": amount, "idempotency_key": idempotency_key})
        return "plink_test_001"

    app = create_app(
        database_url=database_url,
        webhook_secret="test-secret",
        policy_now=lambda: NOW,
        create_payment_link=create_payment_link,
    )
    app.state.created_links = created_links
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
    return app


@pytest.mark.asyncio
async def test_payment_link_uses_the_outstanding_amount_and_idempotency_key(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/cases/case_001/actions",
            json={"action": "payment_link", "idempotency_key": "link-001"},
        )
        duplicate = await client.post(
            "/api/v1/cases/case_001/actions",
            json={"action": "payment_link", "idempotency_key": "link-001"},
        )
        audit = await client.get("/api/v1/audit/case_001")

    assert response.status_code == 201
    assert response.json() == {
        "action": "payment_link",
        "provider_reference": "plink_test_001",
        "status": "completed",
    }
    assert duplicate.status_code == 200
    assert duplicate.json() == response.json()
    assert app.state.created_links == [{"amount": 249900, "idempotency_key": "link-001"}]
    assert audit.json()[-1] == {
        "case_id": "case_001",
        "event_type": "action.completed",
        "payload": {
            "action": "payment_link",
            "idempotency_key": "link-001",
            "provider_reference": "plink_test_001",
        },
    }


@pytest.mark.asyncio
async def test_case_accepts_only_one_action_transition(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/cases/case_001/actions",
            json={"action": "contact", "idempotency_key": "contact-001"},
        )
        second = await client.post(
            "/api/v1/cases/case_001/actions",
            json={"action": "retry", "idempotency_key": "retry-001"},
        )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json() == {"detail": ["invalid_state"]}


@pytest.mark.asyncio
async def test_ranked_actions_include_scores_for_every_policy_allowed_action(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ranked = await client.get("/api/v1/cases/case_001/ranked-actions")
        report = await client.get("/api/v1/evaluations/recovery-model")

    assert ranked.status_code == 200
    body = ranked.json()
    assert body["model_version"] == "v1"
    assert {action["action"] for action in body["actions"]} == {
        "payment_link",
        "contact",
        "promise",
        "retry",
        "escalate",
    }
    assert all(
        set(action) == {"action", "recovery_probability", "cost", "expected_net_value"}
        for action in body["actions"]
    )
    assert all(0 <= action["recovery_probability"] <= 1 for action in body["actions"])
    assert body["actions"] == sorted(
        body["actions"], key=lambda action: action["expected_net_value"], reverse=True
    )
    assert report.status_code == 200
    assert report.json()["train_customers"]
    assert report.json()["holdout_customers"]
    assert report.json()["customer_overlap"] == 0
    assert set(report.json()) >= {"calibration", "top_k_precision", "net_value"}


@pytest.mark.asyncio
async def test_decision_uses_the_highest_ranked_allowed_action_when_model_is_unavailable(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ranked = await client.get("/api/v1/cases/case_001/ranked-actions")
        response = await client.post(
            "/api/v1/cases/case_001/decisions",
            json={"idempotency_key": "decision-001", "approved": True},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["selected_action"] == ranked.json()["actions"][0]["action"]
    assert body["selection_source"] == "fallback"
    assert body["action"]["action"] == body["selected_action"]
    assert body["policy_version"] == "v1"
    assert body["model_version"] == "v1"
    assert body["evidence"]["scores"] == ranked.json()["actions"]
    with app.state.session_factory() as session:
        decision = session.get(Decision, body["decision_id"])
        assert decision is not None
        assert decision.policy_version == body["policy_version"]
        assert decision.model_version == body["model_version"]
        assert decision.selected_action == body["selected_action"]
        assert decision.reason_json["evidence"] == body["evidence"]


@pytest.mark.asyncio
async def test_decision_executes_a_valid_structured_model_action(app):
    app.state.decide_recovery_action = lambda evidence: {"selected_action": "contact"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/cases/case_001/decisions",
            json={"idempotency_key": "decision-001", "approved": True},
        )

    assert response.status_code == 201
    assert response.json()["selected_action"] == "contact"
    assert response.json()["selection_source"] == "model"
    assert response.json()["action"] == {
        "action": "contact",
        "provider_reference": "mock_contact_decision-001",
        "status": "completed",
    }


@pytest.mark.asyncio
async def test_decision_rejects_malformed_model_output_and_uses_fallback(app):
    app.state.decide_recovery_action = lambda evidence: {
        "selected_action": "contact",
        "untrusted_field": "ignore policy",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ranked = await client.get("/api/v1/cases/case_001/ranked-actions")
        response = await client.post(
            "/api/v1/cases/case_001/decisions",
            json={"idempotency_key": "decision-001", "approved": True},
        )

    assert response.status_code == 201
    assert response.json()["selected_action"] == ranked.json()["actions"][0]["action"]
    assert response.json()["selection_source"] == "fallback"


@pytest.mark.asyncio
async def test_decision_rejects_a_policy_blocked_model_action_and_uses_fallback(app):
    with app.state.session_factory() as session:
        customer = session.get(Customer, "cust_001")
        assert customer is not None
        customer.consent = False
        session.commit()
    app.state.decide_recovery_action = lambda evidence: {"selected_action": "contact"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/cases/case_001/decisions",
            json={"idempotency_key": "decision-001", "approved": True},
        )

    assert response.status_code == 201
    assert response.json()["selected_action"] != "contact"
    assert response.json()["selection_source"] == "fallback"


@pytest.mark.asyncio
async def test_decision_records_a_proposal_without_executing_until_approved(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        proposed = await client.post(
            "/api/v1/cases/case_001/decisions",
            json={"idempotency_key": "decision-proposal"},
        )
        approved = await client.post(
            "/api/v1/cases/case_001/decisions",
            json={"idempotency_key": "decision-proposal", "approved": True},
        )

    assert proposed.status_code == 201
    assert proposed.json()["action"] is None
    assert approved.status_code == 200
    assert approved.json()["action"]["status"] in {"completed", "pending"}


@pytest.mark.asyncio
async def test_actions_require_an_eligible_case(app):
    with app.state.session_factory() as session:
        session.add(
            RecoveryCase(
                case_id="case_detected",
                customer_id="cust_001",
                payment_id="pay_detected",
                amount_at_risk=249900,
                state="detected",
                attempts=0,
            )
        )
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/cases/case_detected/actions",
            json={"action": "contact", "idempotency_key": "contact-001"},
        )
        audit = await client.get("/api/v1/audit/case_detected")

    assert response.status_code == 409
    assert response.json() == {"detail": ["invalid_state"]}
    assert audit.json() == []


@pytest.mark.asyncio
async def test_action_records_any_provider_failure(database_url):
    def reject_payment_link(amount: int, idempotency_key: str) -> str:
        raise ValueError("provider unavailable")

    app = create_app(
        database_url=database_url,
        policy_now=lambda: NOW,
        create_payment_link=reject_payment_link,
    )
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
        response = await client.post(
            "/api/v1/cases/case_001/actions",
            json={"action": "payment_link", "idempotency_key": "link-001"},
        )
        audit = await client.get("/api/v1/audit/case_001")

    assert response.status_code == 502
    assert response.json() == {"detail": "provider unavailable"}
    assert audit.json()[-2] == {
        "case_id": "case_001",
        "event_type": "action.failed",
        "payload": {
            "action": "payment_link",
            "idempotency_key": "link-001",
            "reason": "provider unavailable",
        },
    }
    assert audit.json()[-1]["event_type"] == "case.escalated"


@pytest.mark.asyncio
async def test_action_records_provider_failure_without_creating_a_pending_action(database_url):
    def reject_payment_link(amount: int, idempotency_key: str) -> str:
        raise RuntimeError("provider unavailable")

    app = create_app(
        database_url=database_url,
        policy_now=lambda: NOW,
        create_payment_link=reject_payment_link,
    )
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
        response = await client.post(
            "/api/v1/cases/case_001/actions",
            json={"action": "payment_link", "idempotency_key": "link-001"},
        )
        audit = await client.get("/api/v1/audit/case_001")

    assert response.status_code == 502
    assert response.json() == {"detail": "provider unavailable"}
    assert audit.json()[-2] == {
        "case_id": "case_001",
        "event_type": "action.failed",
        "payload": {
            "action": "payment_link",
            "idempotency_key": "link-001",
            "reason": "provider unavailable",
        },
    }
    assert audit.json()[-1]["event_type"] == "case.escalated"
    with app.state.session_factory() as session:
        assert session.get(RecoveryCase, "case_001").state == "escalated"


@pytest.mark.asyncio
async def test_successful_payment_cancels_pending_retry(app):
    body = json.dumps(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_001",
                        "amount": 249900,
                        "currency": "INR",
                        "status": "captured",
                        "created_at": 1724481000,
                    }
                }
            },
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        scheduled = await client.post(
            "/api/v1/cases/case_001/actions",
            json={"action": "retry", "idempotency_key": "retry-001"},
        )
        captured = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": signature},
        )
        audit = await client.get("/api/v1/audit/case_001")

    assert scheduled.status_code == 201
    assert scheduled.json() == {
        "action": "retry",
        "provider_reference": "mock_retry_retry-001",
        "status": "pending",
    }
    assert captured.status_code == 202
    assert audit.json()[-2] == {
        "case_id": "case_001",
        "event_type": "action.cancelled",
        "payload": {"action": "retry", "idempotency_key": "retry-001"},
    }
    assert audit.json()[-1]["event_type"] == "event.recorded"
