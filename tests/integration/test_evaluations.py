import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.tables import Customer, Decision, RecoveryCase
from app.main import create_app

NOW = datetime(2026, 8, 24, 10, tzinfo=UTC)


@pytest.fixture
def app(database_url):
    return create_app(
        database_url=database_url,
        webhook_secret="test-secret",
        policy_now=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_baseline_evaluation_is_deterministic_for_a_seed(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/evaluations/baseline?seed=7")
        second = await client.post("/api/v1/evaluations/baseline?seed=7")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    result = first.json()
    assert result["seed"] == 7
    assert result["case_count"] == 20
    assert result["recovered_amount"] > 0
    assert result["contact_cost"] > 0
    assert result["retry_cost"] > 0
    assert 0 < result["recovery_rate"] < 1


@pytest.mark.asyncio
async def test_published_comparison_matches_reproducible_endpoint_and_dashboard(app):
    directory = Path("app/evaluation")
    expected = json.loads((directory / "published_results.json").read_text())
    exceptions = json.loads((directory / "published_exceptions.json").read_text())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        published = await client.get("/api/v1/evaluations/published")
        reproducible = await client.get("/api/v1/evaluations/reproducible")
        dashboard = await client.get("/api/v1/dashboard")

    assert published.status_code == 200
    assert published.json() == {"results": expected, "exceptions": exceptions}
    assert reproducible.json() == expected
    assert dashboard.json()["evaluation"] == published.json()
    assert len(expected["seeds"]) == 30
    assert all(policy["seed_count"] == 30 for policy in expected["policies"].values())


def _webhook_body(
    payment_id: str, event: str, error_code: str | None = None, customer_id: str | None = None
) -> bytes:
    entity = {
        "id": payment_id,
        "amount": 249900,
        "currency": "INR",
        "status": "captured" if event == "payment.captured" else "failed",
        "created_at": 1724481000,
    }
    if error_code:
        entity["error_code"] = error_code
    if customer_id:
        entity["notes"] = {"customer_id": customer_id}
    return json.dumps(
        {"event": event, "payload": {"payment": {"entity": entity}}}, separators=(",", ":")
    ).encode()


async def _post_webhook(client: AsyncClient, body: bytes):
    signature = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    return await client.post(
        "/api/v1/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": signature},
    )


@pytest.mark.asyncio
async def test_exception_audit_records_match_published_evidence(app):
    expected = json.loads(Path("app/evaluation/published_exceptions.json").read_text())
    with app.state.session_factory() as session:
        session.add_all(
            [
                Customer(customer_id="opt_out_customer", consent=False),
                Customer(customer_id="hard_decline_customer", consent=True),
                Customer(customer_id="provider_customer", consent=True),
                Customer(customer_id="late_customer", consent=True),
                RecoveryCase(
                    case_id="case_provider",
                    customer_id="provider_customer",
                    payment_id="provider",
                    amount_at_risk=249900,
                    state="eligible",
                    attempts=0,
                ),
                RecoveryCase(
                    case_id="case_late",
                    customer_id="late_customer",
                    payment_id="late",
                    amount_at_risk=249900,
                    state="eligible",
                    attempts=0,
                ),
                Decision(
                    decision_id="approval_case_opt_out",
                    case_id="case_opt_out",
                    policy_version="v1",
                    model_version="v1",
                    allowed_actions=["contact"],
                    selected_action="contact",
                    expected_value=1,
                    reason_json={"approval": {"required": True, "granted": True}},
                ),
                Decision(
                    decision_id="approval_case_hard_decline",
                    case_id="case_hard_decline",
                    policy_version="v1",
                    model_version="v1",
                    allowed_actions=["retry"],
                    selected_action="retry",
                    expected_value=1,
                    reason_json={"approval": {"required": True, "granted": True}},
                ),
                Decision(
                    decision_id="approval_case_provider",
                    case_id="case_provider",
                    policy_version="v1",
                    model_version="v1",
                    allowed_actions=["payment_link"],
                    selected_action="payment_link",
                    expected_value=1,
                    reason_json={"approval": {"required": True, "granted": True}},
                ),
                Decision(
                    decision_id="approval_case_late",
                    case_id="case_late",
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

    duplicate_body = _webhook_body("duplicate", "payment.failed")
    opt_out_body = _webhook_body("opt_out", "payment.failed", customer_id="opt_out_customer")
    hard_decline_body = _webhook_body(
        "hard_decline", "payment.failed", "HARD_DECLINE", "hard_decline_customer"
    )
    late_success_body = _webhook_body("late", "payment.captured")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _post_webhook(client, duplicate_body)
        await _post_webhook(client, duplicate_body)
        await _post_webhook(client, opt_out_body)
        await _post_webhook(client, hard_decline_body)
        with app.state.session_factory() as session:
            session.get(RecoveryCase, "case_opt_out").state = "eligible"
            session.get(RecoveryCase, "case_hard_decline").state = "eligible"
            session.commit()
        await client.post(
            "/api/v1/cases/case_opt_out/actions",
            json={"action": "contact", "idempotency_key": "opt-out"},
        )
        await client.post(
            "/api/v1/cases/case_hard_decline/actions",
            json={"action": "retry", "idempotency_key": "hard-decline"},
        )
        await client.post(
            "/api/v1/cases/case_late/actions",
            json={"action": "retry", "idempotency_key": "late-retry"},
        )
        await _post_webhook(client, late_success_body)
        await client.post(
            "/api/v1/cases/case_provider/actions",
            json={"action": "payment_link", "idempotency_key": "provider-failure"},
        )
        audits = {
            name: await client.get(f"/api/v1/audit/{case_id}")
            for name, case_id in {
                "duplicate_delivery": "case_duplicate",
                "late_success": "case_late",
                "opt_out": "case_opt_out",
                "hard_decline": "case_hard_decline",
                "provider_failure": "case_provider",
            }.items()
        }

    assert {
        name: [event["event_type"] for event in response.json()]
        for name, response in audits.items()
    } == expected
