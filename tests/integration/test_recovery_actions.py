import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.tables import AuditEvent, Customer, Decision, RecoveryCase
from app.integrations.razorpay import PaymentLinkReference
from app.main import create_app

NOW = datetime(2026, 8, 24, 10, tzinfo=UTC)


@pytest.fixture
def app(database_url):
    created_links: list[dict[str, int | str]] = []

    def create_payment_link(amount: int, idempotency_key: str) -> str:
        created_links.append({"amount": amount, "idempotency_key": idempotency_key})
        return PaymentLinkReference("https://rzp.io/rzp/test-001", "plink_test_001")

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
                Decision(
                    decision_id="approval_case_001",
                    case_id="case_001",
                    policy_version="v1",
                    model_version="v1",
                    allowed_actions=["payment_link", "contact", "retry", "promise", "escalate"],
                    selected_action="payment_link",
                    expected_value=1,
                    reason_json={"approval": {"required": True, "granted": True}},
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
        "provider_reference": "https://rzp.io/rzp/test-001",
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
            "provider_reference": "https://rzp.io/rzp/test-001",
        },
    }


@pytest.mark.asyncio
async def test_payment_link_action_rejects_a_reference_without_a_provider_id(app):
    app.state.create_payment_link = lambda amount, idempotency_key: "https://rzp.io/rzp/no-id"

    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/cases/case_001/actions",
            json={"action": "payment_link", "idempotency_key": "link-no-provider-id"},
        )
        audit = await client.get("/api/v1/audit/case_001")

    assert response.status_code == 502
    assert response.json() == {"detail": "Razorpay Test Mode payment link creation failed"}
    assert audit.json()[-2]["event_type"] == "action.failed"
    assert audit.json()[-2]["payload"]["reason"] == (
        "Razorpay Test Mode payment link creation failed"
    )
    assert audit.json()[-1]["event_type"] == "case.escalated"


@pytest.mark.asyncio
async def test_case_accepts_only_one_action_transition(app):
    with app.state.session_factory() as session:
        session.add_all(
            [
                Decision(
                    decision_id="approval_contact_case_001",
                    case_id="case_001",
                    policy_version="v1",
                    model_version="v1",
                    allowed_actions=["contact"],
                    selected_action="contact",
                    expected_value=1,
                    reason_json={"approval": {"required": True, "granted": True}},
                ),
                Decision(
                    decision_id="approval_retry_case_001",
                    case_id="case_001",
                    policy_version="v1",
                    model_version="v1",
                    allowed_actions=["retry"],
                    selected_action="retry",
                    expected_value=1,
                    reason_json={"approval": {"required": True, "granted": True}},
                ),
            ]
        )
        session.commit()
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
            headers={"X-Reroute-Role": "business_owner"},
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
            headers={"X-Reroute-Role": "business_owner"},
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
            headers={"X-Reroute-Role": "business_owner"},
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
            headers={"X-Reroute-Role": "business_owner"},
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
        unauthorized = await client.post(
            "/api/v1/cases/case_001/decisions",
            json={"idempotency_key": "decision-proposal", "approved": True},
        )
        approved = await client.post(
            "/api/v1/cases/case_001/decisions",
            json={"idempotency_key": "decision-proposal", "approved": True},
            headers={"X-Reroute-Role": "business_owner"},
        )

    assert proposed.status_code == 201
    assert proposed.json()["action"] is None
    assert unauthorized.status_code == 403
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
async def test_actions_require_an_approved_decision(app):
    with app.state.session_factory() as session:
        session.add(
            RecoveryCase(
                case_id="case_without_approval",
                customer_id="cust_001",
                payment_id="pay_without_approval",
                amount_at_risk=249900,
                state="eligible",
                attempts=0,
            )
        )
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/cases/case_without_approval/actions",
            json={"action": "payment_link", "idempotency_key": "link-without-approval"},
        )

    assert response.status_code == 409
    assert response.json() == {"detail": ["approval_required"]}


@pytest.mark.asyncio
async def test_actions_require_approval_for_the_requested_action(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/cases/case_001/actions",
            json={"action": "contact", "idempotency_key": "contact-unapproved"},
        )

    assert response.status_code == 409
    assert response.json() == {"detail": ["approval_required"]}


@pytest.mark.asyncio
async def test_action_records_any_provider_failure(database_url):
    def reject_payment_link(amount: int, idempotency_key: str) -> str:
        raise ValueError("provider body leaked secret")

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
                Decision(
                    decision_id="approval_case_001",
                    case_id="case_001",
                    policy_version="v1",
                    model_version="v1",
                    allowed_actions=["payment_link"],
                    selected_action="payment_link",
                    expected_value=1,
                    reason_json={"approval": {"required": True, "granted": True}},
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
    assert response.json() == {"detail": "Razorpay Test Mode payment link creation failed"}
    assert "provider body leaked secret" not in response.text
    assert "provider body leaked secret" not in json.dumps(audit.json())
    assert audit.json()[-2] == {
        "case_id": "case_001",
        "event_type": "action.failed",
        "payload": {
            "action": "payment_link",
            "idempotency_key": "link-001",
            "reason": "Razorpay Test Mode payment link creation failed",
            "diagnostic": "payment_link_provider_exception=ValueError",
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
                Decision(
                    decision_id="approval_case_001",
                    case_id="case_001",
                    policy_version="v1",
                    model_version="v1",
                    allowed_actions=["payment_link"],
                    selected_action="payment_link",
                    expected_value=1,
                    reason_json={"approval": {"required": True, "granted": True}},
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
    assert response.json() == {"detail": "Razorpay Test Mode payment link creation failed"}
    assert audit.json()[-2] == {
        "case_id": "case_001",
        "event_type": "action.failed",
        "payload": {
            "action": "payment_link",
            "idempotency_key": "link-001",
            "reason": "Razorpay Test Mode payment link creation failed",
            "diagnostic": "payment_link_provider_exception=RuntimeError",
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
    with app.state.session_factory() as session:
        session.add(
            Decision(
                decision_id="approval_retry_case_001",
                case_id="case_001",
                policy_version="v1",
                model_version="v1",
                allowed_actions=["retry"],
                selected_action="retry",
                expected_value=1,
                reason_json={"approval": {"required": True, "granted": True}},
            )
        )
        session.commit()
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


@pytest.mark.asyncio
async def test_test_mode_trace_requires_human_resume_and_records_2499(database_url):
    calls = 0

    def provider(amount: int, idempotency_key: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("provider unavailable")
        return PaymentLinkReference(
            "https://rzp.io/rzp/recovery", "plink_test_recovery"
        )

    app = create_app(
        database_url=database_url,
        webhook_secret="test-secret",
        policy_now=lambda: NOW,
        create_payment_link=provider,
        decide_recovery_action=lambda evidence: {"selected_action": "payment_link"},
    )
    failure = json.dumps(
        {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_trace",
                        "amount": 249900,
                        "currency": "INR",
                        "method": "upi",
                        "status": "failed",
                        "error_code": "BAD_REQUEST_ERROR",
                        "error_description": "insufficient funds",
                        "created_at": 1724481000,
                        "notes": {"customer_id": "cust_trace"},
                        "order_id": "order_trace",
                    }
                }
            },
        },
        separators=(",", ":"),
    ).encode()
    captured = json.dumps(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_trace",
                        "amount": 249900,
                        "currency": "INR",
                        "method": "upi",
                        "status": "captured",
                        "created_at": 1724481100,
                        "notes": {"customer_id": "cust_trace"},
                        "order_id": "order_trace",
                    }
                }
            },
        },
        separators=(",", ":"),
    ).encode()
    with app.state.session_factory() as session:
        session.add(Customer(customer_id="cust_trace", consent=True))
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        failure_response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=failure,
            headers={
                "X-Razorpay-Signature": hmac.new(
                    b"test-secret", failure, hashlib.sha256
                ).hexdigest()
            },
        )
        investigated = await client.post("/api/v1/cases/case_order_trace/investigate")
        proposal = await client.post(
            "/api/v1/cases/case_order_trace/decisions",
            json={"idempotency_key": "trace-decision"},
        )
        bypassed = await client.post(
            "/api/v1/cases/case_order_trace/actions",
            json={"action": "payment_link", "idempotency_key": "trace-bypass"},
        )
        failed = await client.post(
            "/api/v1/cases/case_order_trace/decisions",
            json={"idempotency_key": "trace-decision", "approved": True},
            headers={"X-Reroute-Role": "business_owner"},
        )
        denied_resume = await client.post(
            "/api/v1/cases/case_order_trace/resume",
            json={"idempotency_key": "trace-resume"},
        )
        resumed = await client.post(
            "/api/v1/cases/case_order_trace/resume",
            json={"idempotency_key": "trace-resume"},
            headers={"X-Reroute-Role": "business_owner"},
        )
        resumed_duplicate = await client.post(
            "/api/v1/cases/case_order_trace/resume",
            json={"idempotency_key": "trace-resume"},
            headers={"X-Reroute-Role": "business_owner"},
        )
        completed = await client.post(
            "/api/v1/cases/case_order_trace/decisions",
            json={"idempotency_key": "trace-success", "approved": True},
            headers={"X-Reroute-Role": "business_owner"},
        )
        capture_response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=captured,
            headers={
                "X-Razorpay-Signature": hmac.new(
                    b"test-secret", captured, hashlib.sha256
                ).hexdigest()
            },
        )
        dashboard = await client.get("/api/v1/dashboard")
        outcome = await client.get("/api/v1/cases/case_order_trace/outcome")
        audit = await client.get("/api/v1/audit/case_order_trace")

    assert failure_response.status_code == 202
    assert investigated.status_code == 200
    assert investigated.json()["new_state"] == "eligible"
    assert investigated.json()["policy"]["allowed_actions"]
    assert proposal.status_code == 201 and proposal.json()["action"] is None
    assert bypassed.status_code == 409
    assert bypassed.json() == {"detail": ["approval_required"]}
    assert failed.status_code == 502
    assert denied_resume.status_code == 403
    assert resumed.json() == {
        "case_id": "case_order_trace",
        "previous_state": "escalated",
        "new_state": "eligible",
    }
    assert resumed_duplicate.status_code == 200
    assert resumed_duplicate.json() == resumed.json()
    assert completed.status_code == 201
    assert capture_response.status_code == 202
    outcome_body = outcome.json()
    assert outcome_body["outcome"] == {
        "recovered": True,
        "recovered_amount": 249900,
        "contact_cost": 0,
        "discount_cost": 0,
        "resolved_at": outcome_body["outcome"]["resolved_at"],
        "source": "razorpay_test",
    }
    assert outcome_body["outcome"]["resolved_at"].startswith("2024-08-24T06:31:40")
    assert outcome_body["evidence"] == {
        "event_id": f"evt_{hashlib.sha256(captured).hexdigest()}",
        "provider_event_id": hashlib.sha256(captured).hexdigest(),
        "payment_id": "pay_trace",
        "obligation_reference": "order_trace",
        "amount": 249900,
        "occurred_at": "2024-08-24T06:31:40+00:00",
        "source": "razorpay_test",
    }
    assert dashboard.json()["executive"]["test_mode_value"] == 249900
    event_types = [event["event_type"] for event in audit.json()]
    assert event_types.count("outcome.recorded") == 1
    assert "human.approval_required" in event_types
    assert "human.approval_granted" in event_types
    assert "case.escalated" in event_types
    assert audit.json()[event_types.index("case.escalated")]["payload"]["owner"] == "business_owner"
    assert calls == 2


@pytest.mark.asyncio
async def test_resume_rechecks_current_policy_and_leaves_blocked_case_escalated(database_url):
    app = create_app(
        database_url=database_url,
        webhook_secret="test-secret",
        policy_now=lambda: NOW,
        kill_switch=True,
    )
    with app.state.session_factory() as session:
        session.add_all(
            [
                Customer(customer_id="cust_resume", consent=True),
                RecoveryCase(
                    case_id="case_resume",
                    customer_id="cust_resume",
                    payment_id="pay_resume",
                    amount_at_risk=249900,
                    state="escalated",
                    attempts=0,
                ),
            ]
        )
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        blocked = await client.post(
            "/api/v1/cases/case_resume/resume",
            json={"idempotency_key": "resume-policy"},
            headers={"X-Reroute-Role": "business_owner"},
        )
        cases_after_block = await client.get("/api/v1/cases")
        audit = await client.get("/api/v1/audit/case_resume")
        app.state.kill_switch = False
        resumed = await client.post(
            "/api/v1/cases/case_resume/resume",
            json={"idempotency_key": "resume-policy"},
            headers={"X-Reroute-Role": "business_owner"},
        )

    assert blocked.status_code == 409
    assert blocked.json()["detail"] == {
        action: ["kill_switch"]
        for action in ["payment_link", "contact", "retry", "promise", "escalate"]
    }
    assert [case for case in cases_after_block.json() if case["case_id"] == "case_resume"][0][
        "state"
    ] == "escalated"
    assert audit.json()[-1]["event_type"] == "case.resume_blocked"
    assert resumed.status_code == 200
    assert resumed.json() == {
        "case_id": "case_resume",
        "previous_state": "escalated",
        "new_state": "eligible",
    }


@pytest.mark.asyncio
async def test_resume_idempotency_key_cannot_cross_cases(app):
    with app.state.session_factory() as session:
        case = session.get(RecoveryCase, "case_001")
        assert case is not None
        case.state = "escalated"
        session.add(
            RecoveryCase(
                case_id="case_002",
                customer_id="cust_001",
                payment_id="pay_002",
                amount_at_risk=249900,
                state="escalated",
                attempts=0,
            )
        )
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/cases/case_001/resume",
            json={"idempotency_key": "shared-resume"},
            headers={"X-Reroute-Role": "business_owner"},
        )
        second = await client.post(
            "/api/v1/cases/case_002/resume",
            json={"idempotency_key": "shared-resume"},
            headers={"X-Reroute-Role": "business_owner"},
        )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json() == {"detail": "idempotency key belongs to another case"}


def test_audit_events_are_append_only(app):
    with app.state.session_factory() as session:
        session.add(AuditEvent(case_id="case_001", event_type="test.audit", payload={"ok": True}))
        session.commit()
        with pytest.raises(IntegrityError, match="audit events are immutable"):
            session.execute(
                text("UPDATE audit_events SET event_type = 'tampered' WHERE case_id = 'case_001'")
            )
            session.commit()
        session.rollback()
        with pytest.raises(IntegrityError, match="audit events are immutable"):
            session.execute(text("DELETE FROM audit_events WHERE case_id = 'case_001'"))
            session.commit()
