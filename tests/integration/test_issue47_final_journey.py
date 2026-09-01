import csv
import hashlib
import hmac
import json
from collections import Counter
from datetime import UTC, datetime
from io import StringIO

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.tables import (
    ActionEvent,
    AuditEvent,
    CheckoutOrder,
    Customer,
    Decision,
    Outcome,
    PaymentEvent,
    RecoveryCase,
)
from app.integrations.razorpay import PaymentLinkReference
from app.main import create_app
from simulator.generator import generate_csv

WEBHOOK_SECRET = "issue47-test-secret"
HISTORY_SEED = 47
HISTORY_COUNT = 999
DUMBBELL_AMOUNT = 249_900
NOW = datetime(2026, 8, 24, 10, tzinfo=UTC)

# The simulator contract is supplied by de72fb7.  Recovery-link correlation is
# exercised through the public webhook and persisted ActionEvent reference; the
# capture deliberately does not repeat the original PaymentObligation in its body.


@pytest.fixture
def app(database_url):
    provider_calls: list[dict[str, int | str]] = []

    def create_payment_link(amount: int, idempotency_key: str) -> str:
        provider_calls.append({"amount": amount, "idempotency_key": idempotency_key})
        return PaymentLinkReference(
            "plink_test_issue47_recovery", "plink_test_issue47_recovery"
        )

    application = create_app(
        database_url=database_url,
        webhook_secret=WEBHOOK_SECRET,
        policy_now=lambda: NOW,
        create_payment_link=create_payment_link,
    )
    application.state.provider_calls = provider_calls
    return application


def _seed_checkout_order(app, order_id: str) -> None:
    with app.state.session_factory() as session:
        session.add(
            CheckoutOrder(
                checkout_id=f"checkout_{order_id}",
                idempotency_key=f"seed_{order_id}",
                provider_order_id=order_id,
                obligation_reference=order_id,
                product_code="dumbbell_5kg",
                product_name="5 kg Dumbbell",
                amount=DUMBBELL_AMOUNT,
                currency="INR",
                status="created",
                provider="razorpay_test",
            )
        )
        session.commit()


def _payload(
    event_type: str,
    *,
    payment_id: str,
    order_id: str | None,
    customer_id: str,
    event_id: str,
    status: str,
    created_at: int = 1787529600,
    error_code: str | None = None,
    payment_link_id: str | None = None,
) -> bytes:
    notes: dict[str, str] = {"customer_id": customer_id}
    entity: dict[str, object] = {
        "id": payment_id,
        "amount": DUMBBELL_AMOUNT,
        "currency": "INR",
        "method": "card",
        "status": status,
        "created_at": created_at,
        "notes": notes,
    }
    if order_id is not None:
        entity["order_id"] = order_id
        notes["obligation_reference"] = order_id
    if payment_link_id is not None:
        entity["payment_link_id"] = payment_link_id
    if error_code is not None:
        entity.update(
            {
                "error_code": error_code,
                "error_description": "payment failed in Test Mode",
            }
        )
    return json.dumps(
        {
            "entity": "event",
            # The provider event ID is signed payload data; the header below
            # is only a presentation/replay fixture and cannot override it.
            "id": event_id,
            "event": event_type,
            "payload": {"payment": {"entity": entity}},
        },
        separators=(",", ":"),
    ).encode()


def _webhook_headers(body: bytes, event_id: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Razorpay-Event-Id": event_id,
        "X-Razorpay-Signature": hmac.new(
            WEBHOOK_SECRET.encode(), body, hashlib.sha256
        ).hexdigest(),
    }


@pytest.mark.asyncio
async def test_issue47_999_history_is_deterministic_idempotent_and_ranked(app):
    """Contract for the simulator branch's revised 999-payment launch corpus.

    The public ``generate_csv`` seam is intentionally used here.  This test keeps
    the exact cohort contract visible and fails loudly if the rejected
    failure-heavy population returns; it does not encode that old dataset.
    """
    # The default call is part of the public contract; the explicit values below
    # make the expected seed/count visible without importing implementation-only
    # constants from the parallel simulator branch.
    content = generate_csv()
    assert content == generate_csv(seed=HISTORY_SEED, event_count=HISTORY_COUNT)
    assert hashlib.sha256(content.encode()).hexdigest() == (
        "b31cf603e91fcde0140c4c110dfc37202c116d703cbb574af8137b93ff047cc1"
    )
    rows = list(csv.DictReader(StringIO(content)))

    assert len(rows) == HISTORY_COUNT
    assert len({row["event_id"] for row in rows}) == HISTORY_COUNT
    assert len({row["payment_id"] for row in rows}) == HISTORY_COUNT
    assert [row["event_id"] for row in rows[:992]] == [
        f"demo_event_{index:05d}" for index in range(992)
    ]
    assert [row["event_id"] for row in rows[-7:]] == [
        "demo_hard_decline",
        "demo_provider_failure",
        "demo_opt_out",
        "demo_promise",
        "demo_eligible",
        "demo_isolated_a",
        "demo_isolated_b",
    ]
    status_counts = Counter(row["status"] for row in rows)
    assert status_counts == Counter({"failed": 250, "captured": 749})
    failure_rate = status_counts["failed"] / len(rows)
    assert failure_rate == pytest.approx(250 / HISTORY_COUNT)
    assert 0.20 <= failure_rate <= 0.30
    method_counts = Counter(row["method"] for row in rows)
    assert method_counts == Counter({"upi": 450, "card": 275, "netbanking": 274})
    method_failure_counts = Counter(
        row["method"] for row in rows if row["status"] == "failed"
    )
    assert method_failure_counts == Counter({"upi": 225, "card": 13, "netbanking": 12})
    assert method_failure_counts["upi"] / method_counts["upi"] == pytest.approx(0.5)
    assert method_failure_counts["card"] / method_counts["card"] < 0.15
    assert method_failure_counts["netbanking"] / method_counts["netbanking"] < 0.15
    obligation_references = [
        row["obligation_reference"] for row in rows if row["obligation_reference"]
    ]
    assert len(obligation_references) == 997
    assert len(obligation_references) == len(set(obligation_references))
    assert {row["method"] for row in rows} == {"upi", "card", "netbanking"}
    assert len({row["amount"] for row in rows}) >= 3
    assert len({row["occurred_at"] for row in rows}) >= 3
    assert len({row["customer_id"] for row in rows}) >= 3
    assert len({row["error_reason"] for row in rows}) >= 2
    assert any(row["successful_payments"] == "0" for row in rows)
    assert any(int(row["successful_payments"]) > 0 for row in rows)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/data/import", content=content, headers={"Content-Type": "text/csv"}
        )
        first_cases = await client.get("/api/v1/cases")
        findings_before_detection = await client.get("/api/v1/findings")
        second = await client.post(
            "/api/v1/data/import", content=content, headers={"Content-Type": "text/csv"}
        )
        findings_response = await client.post("/api/v1/findings/detect")
        reproducible_evaluation = await client.get("/api/v1/evaluations/reproducible")

    assert first.status_code == 201
    assert first.json() == {"imported": 999, "duplicates": 0}
    assert second.status_code == 201
    assert second.json() == {"imported": 0, "duplicates": 999}
    assert findings_before_detection.status_code == 200
    assert findings_before_detection.json() == []

    with app.state.session_factory() as session:
        events = session.scalars(select(PaymentEvent).order_by(PaymentEvent.event_id)).all()
        cases_after_replay = session.scalars(select(RecoveryCase)).all()
        assert len(events) == 999
        assert len(cases_after_replay) == len(first_cases.json())
        assert all(event.provider != "razorpay_test" for event in events)
        assert all(event.raw_body is None for event in events)
        assert session.scalar(select(Outcome.outcome_id)) is None
        assert session.scalar(select(ActionEvent.action_id)) is None

    assert findings_response.status_code == 200
    findings = findings_response.json()
    assert len(findings) == 37
    assert all(finding["detector_version"] == "leak-detector-v1" for finding in findings)
    assert all(
        findings[index]["recoverable_impact"] >= findings[index + 1]["recoverable_impact"]
        for index in range(len(findings) - 1)
    )
    top_finding = findings[0]
    assert top_finding["cohort_filter"] == {"dimension": "method", "value": "upi"}
    assert top_finding["baseline_rate"] == pytest.approx(250 / HISTORY_COUNT)
    assert top_finding["observed_rate"] == pytest.approx(0.5)
    assert top_finding["impact"] == 23_315_438
    assert top_finding["recoverable_impact"] == 11_657_719
    assert top_finding["evidence"] == {
        "event_ids": top_finding["evidence"]["event_ids"],
        "support": 450,
        "failure_count": 225,
        "attempted_value": 93_355_200,
        "failed_value": 46_177_700,
        "unresolved_value": 46_177_700,
        "recovery_probability": 0.5,
        "data_quality_warnings": [],
    }
    assert findings[1]["cohort_filter"] == {
        "dimension": "error_source",
        "value": "bank",
    }
    assert findings[1]["recoverable_impact"] == 10_416_686
    event_by_id = {event["event_id"]: event for event in rows}
    assert set(top_finding["evidence"]["event_ids"]) == {
        event_id for event_id, event in event_by_id.items() if event["method"] == "upi"
    }
    for finding in findings:
        evidence = finding["evidence"]
        event_ids = evidence["event_ids"]
        assert evidence["support"] == len(event_ids) >= 3
        assert set(event_ids) <= event_by_id.keys()
        failure_count = sum(event_by_id[event_id]["status"] == "failed" for event_id in event_ids)
        assert evidence["failure_count"] == failure_count
        assert finding["observed_rate"] == pytest.approx(failure_count / len(event_ids))
        assert finding["observed_rate"] > finding["baseline_rate"]

    top_finding = findings[0]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        retrieved = await client.get(f"/api/v1/findings/{top_finding['finding_id']}")
    assert retrieved.status_code == 200
    assert retrieved.json() == top_finding

    assert reproducible_evaluation.status_code == 200
    evaluation = reproducible_evaluation.json()
    assert evaluation["seeds"] == list(range(30))
    assert evaluation["cases_per_seed"] == 30
    assert set(evaluation["policies"]) == {"adaptive", "rules_based", "fixed"}


@pytest.mark.asyncio
async def test_issue47_999_detector_ranking_is_proven_at_the_http_seam(app):
    """Detector ranking is independently executable while corpus metrics evolve."""
    content = generate_csv(seed=HISTORY_SEED, event_count=999)
    rows = list(csv.DictReader(StringIO(content)))
    event_by_id = {event["event_id"]: event for event in rows}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        imported = await client.post("/api/v1/data/import", content=content)
        findings_response = await client.post("/api/v1/findings/detect")
        reproducible_evaluation = await client.get("/api/v1/evaluations/reproducible")

    assert imported.status_code == 201
    assert imported.json() == {"imported": 999, "duplicates": 0}
    with app.state.session_factory() as session:
        events = session.scalars(select(PaymentEvent)).all()
        assert len(events) == 999
        assert all(event.provider != "razorpay_test" for event in events)
        assert all(event.raw_body is None for event in events)
        assert session.scalar(select(Outcome.outcome_id)) is None
    assert findings_response.status_code == 200
    findings = findings_response.json()
    assert findings
    assert all(finding["detector_version"] == "leak-detector-v1" for finding in findings)
    assert all(
        findings[index]["recoverable_impact"] >= findings[index + 1]["recoverable_impact"]
        for index in range(len(findings) - 1)
    )
    for finding in findings:
        evidence = finding["evidence"]
        event_ids = evidence["event_ids"]
        assert evidence["support"] == len(event_ids) >= 3
        assert set(event_ids) <= event_by_id.keys()
        failure_count = sum(event_by_id[event_id]["status"] == "failed" for event_id in event_ids)
        assert evidence["failure_count"] == failure_count
        assert finding["observed_rate"] == pytest.approx(failure_count / len(event_ids))
        assert finding["observed_rate"] > finding["baseline_rate"]

    top_finding = findings[0]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        retrieved = await client.get(f"/api/v1/findings/{top_finding['finding_id']}")
    assert retrieved.status_code == 200
    assert retrieved.json() == top_finding

    assert reproducible_evaluation.status_code == 200
    evaluation = reproducible_evaluation.json()
    assert evaluation["seeds"] == list(range(30))
    assert evaluation["cases_per_seed"] == 30
    assert set(evaluation["policies"]) == {"adaptive", "rules_based", "fixed"}


@pytest.mark.asyncio
async def test_issue47_signed_failure_replay_preserves_order_correlation_and_provenance(app):
    content = generate_csv(seed=HISTORY_SEED, event_count=999)
    with app.state.session_factory() as session:
        session.add(Customer(customer_id="cust_live_1000", consent=True))
        session.commit()
    _seed_checkout_order(app, "order_live_1000")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        imported = await client.post("/api/v1/data/import", content=content)
        body = _payload(
            "payment.failed",
            payment_id="pay_live_1000",
            order_id="order_live_1000",
            customer_id="cust_live_1000",
            event_id="event_live_failed_1000",
            status="failed",
            error_code="BAD_REQUEST_ERROR",
        )
        first = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers=_webhook_headers(body, "event_live_failed_1000"),
        )
        duplicate = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers=_webhook_headers(body, "event_live_failed_1000"),
        )
        cases = await client.get("/api/v1/cases")
        audit = await client.get("/api/v1/audit/case_order_live_1000")

    assert imported.json() == {"imported": 999, "duplicates": 0}
    assert first.status_code == 202
    assert first.json() == {
        "event_id": "evt_event_live_failed_1000",
        "status": "accepted",
    }
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "event_id": "evt_event_live_failed_1000",
        "status": "duplicate",
    }
    live_cases = [case for case in cases.json() if case["case_id"] == "case_order_live_1000"]
    assert live_cases == [
        {
            "amount_at_risk": DUMBBELL_AMOUNT,
            "attempts": 0,
            "case_id": "case_order_live_1000",
            "customer_id": "cust_live_1000",
            "payment_id": "pay_live_1000",
            "obligation_reference": "order_live_1000",
            "state": "detected",
            "stop_reason": None,
        }
    ]

    with app.state.session_factory() as session:
        live_events = session.scalars(
            select(PaymentEvent).where(PaymentEvent.obligation_reference == "order_live_1000")
        ).all()
        assert len(live_events) == 1
        event = live_events[0]
        assert event.event_id == "evt_event_live_failed_1000"
        assert event.provider_event_id == "event_live_failed_1000"
        assert event.event_type == "payment.failed"
        assert event.payment_id == "pay_live_1000"
        assert event.customer_id == "cust_live_1000"
        assert event.obligation_reference == "order_live_1000"
        assert event.amount == DUMBBELL_AMOUNT
        assert event.currency == "INR"
        assert event.status == "failed"
        assert event.error_code == "BAD_REQUEST_ERROR"
        assert event.provider == "razorpay_test"
        assert event.raw_body == body
        assert event.raw_hash == hashlib.sha256(body).hexdigest()
        assert session.scalar(select(func.count()).select_from(PaymentEvent)) == 1000
        assert session.scalar(select(Decision.decision_id)) is None
        assert session.scalar(select(ActionEvent.action_id)) is None
        assert session.scalar(select(Outcome.outcome_id)) is None

    audit_types = [event["event_type"] for event in audit.json()]
    assert audit_types.count("case.detected") == 1
    assert audit_types.count("event.recorded") == 1
    assert audit_types.count("event.duplicate") == 1
    detected = next(event for event in audit.json() if event["event_type"] == "case.detected")
    assert detected["payload"] == {
        "payment_id": "pay_live_1000",
        "obligation_reference": "order_live_1000",
    }


@pytest.mark.asyncio
async def test_issue47_changed_signed_body_replay_cannot_mutate_event_case_or_outcome(app):
    """A provider ID is immutable even when a replay carries different signed bytes.

    The public webhook contract reports an exact replay as ``duplicate`` but
    distinguishes a same-ID body change as a conflict.  Both paths are safe only
    because the original raw body/hash and derived state are never replaced; it
    also keeps this test independent of presentation code.
    """
    body = _payload(
        "payment.failed",
        payment_id="pay_immutable_1000",
        order_id="order_immutable_1000",
        customer_id="cust_immutable_1000",
        event_id="event_immutable_failed_1000",
        status="failed",
        error_code="BAD_REQUEST_ERROR",
    )
    changed_body = body.replace(b"BAD_REQUEST_ERROR", b"NETWORK_ERROR")
    assert changed_body != body
    assert hashlib.sha256(changed_body).hexdigest() != hashlib.sha256(body).hexdigest()
    _seed_checkout_order(app, "order_immutable_1000")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers=_webhook_headers(body, "event_immutable_failed_1000"),
        )
        cases_before = await client.get("/api/v1/cases")
        outcome_before = await client.get(
            "/api/v1/cases/case_order_immutable_1000/outcome"
        )
        changed_replay = await client.post(
            "/api/v1/webhooks/razorpay",
            content=changed_body,
            headers=_webhook_headers(changed_body, "event_immutable_failed_1000"),
        )
        cases_after = await client.get("/api/v1/cases")
        outcome_after = await client.get("/api/v1/cases/case_order_immutable_1000/outcome")
        audit = await client.get("/api/v1/audit/case_order_immutable_1000")

    assert first.status_code == 202
    assert changed_replay.status_code == 409
    assert changed_replay.json() == {
        "detail": "provider event body conflicts with stored event"
    }
    assert cases_before.json() == cases_after.json() == [
        {
            "amount_at_risk": DUMBBELL_AMOUNT,
            "attempts": 0,
            "case_id": "case_order_immutable_1000",
            "customer_id": "cust_immutable_1000",
            "payment_id": "pay_immutable_1000",
            "obligation_reference": "order_immutable_1000",
            "state": "detected",
            "stop_reason": None,
        }
    ]
    assert outcome_before.json() == outcome_after.json() == {
        "case_id": "case_order_immutable_1000",
        "outcome": None,
        "evidence": None,
    }
    with app.state.session_factory() as session:
        event = session.get(PaymentEvent, "evt_event_immutable_failed_1000")
        assert event is not None
        assert event.provider_event_id == "event_immutable_failed_1000"
        assert event.payment_id == "pay_immutable_1000"
        assert event.error_code == "BAD_REQUEST_ERROR"
        assert event.raw_body == body
        assert event.raw_hash == hashlib.sha256(body).hexdigest()
        assert session.scalar(select(func.count()).select_from(PaymentEvent)) == 1
        assert session.scalar(select(Outcome.outcome_id)) is None
    assert [event["event_type"] for event in audit.json()].count("event.duplicate") == 0


@pytest.mark.asyncio
async def test_issue47_recovery_requires_policy_and_approval_then_records_one_test_mode_outcome(
    app,
):
    content = generate_csv(seed=HISTORY_SEED, event_count=999)
    with app.state.session_factory() as session:
        session.add(Customer(customer_id="cust_live_1000", consent=True))
        session.commit()
    _seed_checkout_order(app, "order_live_1000")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        imported = await client.post("/api/v1/data/import", content=content)
        failure = _payload(
            "payment.failed",
            payment_id="pay_live_1000",
            order_id="order_live_1000",
            customer_id="cust_live_1000",
            event_id="event_live_failed_1000",
            status="failed",
            error_code="BAD_REQUEST_ERROR",
        )
        failure_response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=failure,
            headers=_webhook_headers(failure, "event_live_failed_1000"),
        )
        policy_before_investigation = await client.get(
            "/api/v1/cases/case_order_live_1000/policy"
        )
        ranked_before_investigation = await client.get(
            "/api/v1/cases/case_order_live_1000/ranked-actions"
        )
        investigated = await client.post("/api/v1/cases/case_order_live_1000/investigate")
        proposal = await client.post(
            "/api/v1/cases/case_order_live_1000/decisions",
            json={"idempotency_key": "issue47-decision", "selected_action": "payment_link"},
        )
        bypass = await client.post(
            "/api/v1/cases/case_order_live_1000/actions",
            json={"action": "payment_link", "idempotency_key": "issue47-bypass"},
        )
        unauthorized = await client.post(
            "/api/v1/cases/case_order_live_1000/decisions",
            json={
                "idempotency_key": "issue47-decision",
                "selected_action": "payment_link",
                "approved": True,
            },
        )
        approved = await client.post(
            "/api/v1/cases/case_order_live_1000/decisions",
            json={
                "idempotency_key": "issue47-decision",
                "selected_action": "payment_link",
                "approved": True,
            },
            headers={"X-Reroute-Role": "business_owner"},
        )
        approved_duplicate = await client.post(
            "/api/v1/cases/case_order_live_1000/decisions",
            json={
                "idempotency_key": "issue47-decision",
                "selected_action": "payment_link",
                "approved": True,
            },
            headers={"X-Reroute-Role": "business_owner"},
        )
        outcome_before_capture = await client.get(
            "/api/v1/cases/case_order_live_1000/outcome"
        )
        provider_reference = approved.json()["action"]["provider_reference"]
        capture = _payload(
            "payment.captured",
            payment_id="pay_recovery_1000",
            order_id=None,
            customer_id="cust_live_1000",
            event_id="event_live_capture_1000",
            status="captured",
            created_at=1787529660,
            payment_link_id=provider_reference,
        )
        capture_response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=capture,
            headers=_webhook_headers(capture, "event_live_capture_1000"),
        )
        capture_duplicate = await client.post(
            "/api/v1/webhooks/razorpay",
            content=capture,
            headers=_webhook_headers(capture, "event_live_capture_1000"),
        )
        outcome = await client.get("/api/v1/cases/case_order_live_1000/outcome")
        audit = await client.get("/api/v1/audit/case_order_live_1000")

    assert imported.json() == {"imported": 999, "duplicates": 0}
    assert failure_response.status_code == 202
    assert policy_before_investigation.json()["allowed_actions"] == [
        "payment_link",
        "contact",
        "retry",
        "promise",
        "escalate",
    ]
    assert policy_before_investigation.json()["blocked_reasons"] == {}
    assert {
        action["action"] for action in ranked_before_investigation.json()["actions"]
    } == set(policy_before_investigation.json()["allowed_actions"])
    assert investigated.status_code == 200
    assert investigated.json()["new_state"] == "eligible"
    assert proposal.status_code == 201
    assert proposal.json()["action"] is None
    assert bypass.status_code == 409
    assert bypass.json() == {"detail": ["approval_required"]}
    assert unauthorized.status_code == 403
    # Approval of an existing proposal is an idempotent update of that decision.
    assert approved.status_code == 200
    assert approved.json()["action"] == {
        "action": "payment_link",
        "provider_reference": "plink_test_issue47_recovery",
        "status": "completed",
    }
    assert approved_duplicate.status_code == 200
    assert approved_duplicate.json() == approved.json()
    assert outcome_before_capture.json() == {
        "case_id": "case_order_live_1000",
        "outcome": None,
        "evidence": None,
    }
    assert app.state.provider_calls == [
        {"amount": DUMBBELL_AMOUNT, "idempotency_key": "issue47-decision"}
    ]
    assert provider_reference == "plink_test_issue47_recovery"
    # The capture has only the provider recovery-link reference. Its durable
    # PaymentObligation must be resolved from the persisted ActionEvent, not
    # copied into this provider payload by the test.
    assert b'"order_id"' not in capture
    assert b'"obligation_reference"' not in capture
    assert b'"payment_link_id":"plink_test_issue47_recovery"' in capture
    assert capture_response.status_code == 202
    assert capture_response.json() == {
        "event_id": "evt_event_live_capture_1000",
        "status": "accepted",
    }
    assert capture_duplicate.status_code == 200
    assert capture_duplicate.json() == {
        "event_id": "evt_event_live_capture_1000",
        "status": "duplicate",
    }

    assert outcome.status_code == 200
    outcome_body = outcome.json()
    assert outcome_body["outcome"] == {
        "recovered": True,
        "recovered_amount": DUMBBELL_AMOUNT,
        "contact_cost": 0,
        "discount_cost": 0,
        "resolved_at": outcome_body["outcome"]["resolved_at"],
        "source": "razorpay_test",
    }
    assert outcome_body["evidence"] == {
        "event_id": "evt_event_live_capture_1000",
        "provider_event_id": "event_live_capture_1000",
        "payment_id": "pay_recovery_1000",
        "obligation_reference": "order_live_1000",
        "amount": DUMBBELL_AMOUNT,
        "occurred_at": "2026-08-24T00:01:00+00:00",
        "source": "razorpay_test",
    }

    with app.state.session_factory() as session:
        events = session.scalars(
            select(PaymentEvent).where(PaymentEvent.obligation_reference == "order_live_1000")
        ).all()
        case = session.get(RecoveryCase, "case_order_live_1000")
        decisions = session.scalars(
            select(Decision).where(Decision.case_id == "case_order_live_1000")
        ).all()
        actions = session.scalars(
            select(ActionEvent).where(ActionEvent.case_id == "case_order_live_1000")
        ).all()
        outcomes = session.scalars(
            select(Outcome).where(Outcome.case_id == "case_order_live_1000")
        ).all()
        assert len(events) == 2
        assert {event.payment_id for event in events} == {"pay_live_1000", "pay_recovery_1000"}
        failure_event = next(event for event in events if event.event_type == "payment.failed")
        capture_event = next(event for event in events if event.event_type == "payment.captured")
        assert failure_event.provider == "razorpay_test"
        assert capture_event.provider == "razorpay_test"
        assert capture_event.raw_body == capture
        assert capture_event.raw_hash == hashlib.sha256(capture).hexdigest()
        assert capture_event.provider_event_id == "event_live_capture_1000"
        assert capture_event.obligation_reference == "order_live_1000"
        assert case is not None
        assert case.state == "recovered"
        assert len(decisions) == 1
        assert len(actions) == 1
        assert actions[0].provider_reference == provider_reference
        assert actions[0].status == "completed"
        assert len(outcomes) == 1
        assert outcomes[0].source == "razorpay_test"
        assert session.scalar(select(func.count()).select_from(Outcome)) == 1

    audit_types = [event["event_type"] for event in audit.json()]
    assert audit_types.count("human.approval_required") == 1
    assert audit_types.count("human.approval_granted") == 1
    assert audit_types.count("outcome.recorded") == 1
    assert audit_types.count("event.duplicate") == 1
    assert audit_types.count("action.completed") == 1
    assert audit_types.count("action.started") == 1
    assert audit_types.count("action.blocked") == 1
    blocked = next(event for event in audit.json() if event["event_type"] == "action.blocked")
    assert blocked["payload"]["reasons"] == ["approval_required"]


@pytest.mark.asyncio
async def test_issue47_webhook_customer_metadata_does_not_grant_consent(app):
    body = _payload(
        "payment.failed",
        payment_id="pay_no_consent_1000",
        order_id="order_no_consent_1000",
        customer_id="cust_no_consent_1000",
        event_id="event_no_consent_failed_1000",
        status="failed",
        error_code="BAD_REQUEST_ERROR",
    )
    _seed_checkout_order(app, "order_no_consent_1000")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers=_webhook_headers(body, "event_no_consent_failed_1000"),
        )
        policy = await client.get("/api/v1/cases/case_order_no_consent_1000/policy")

    assert response.status_code == 202
    assert policy.json()["blocked_reasons"] == {
        "payment_link": ["missing_consent"],
        "contact": ["missing_consent"],
        "promise": ["missing_consent"],
    }
    with app.state.session_factory() as session:
        customer = session.get(Customer, "cust_no_consent_1000")
        assert customer is not None
        assert customer.consent is False


@pytest.mark.asyncio
async def test_issue47_standalone_success_is_not_a_recovery_outcome(app):
    """A captured checkout without a preceding failed obligation is standalone.

    It is valid provider evidence, but it is not the #1000 failure-first
    recovery journey and therefore must not create a RecoveryCase or Outcome.
    """
    body = _payload(
        "payment.captured",
        payment_id="pay_standalone_1000",
        order_id="order_standalone_1000",
        customer_id="cust_standalone_1000",
        event_id="event_standalone_capture_1000",
        status="captured",
    )
    _seed_checkout_order(app, "order_standalone_1000")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers=_webhook_headers(body, "event_standalone_capture_1000"),
        )
        duplicate = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers=_webhook_headers(body, "event_standalone_capture_1000"),
        )
        cases = await client.get("/api/v1/cases")

    assert response.status_code == 202
    assert duplicate.status_code == 200
    assert cases.json() == []
    with app.state.session_factory() as session:
        assert session.scalar(
            select(PaymentEvent).where(PaymentEvent.payment_id == "pay_standalone_1000")
        )
        assert session.scalar(
            select(RecoveryCase).where(RecoveryCase.payment_id == "pay_standalone_1000")
        ) is None
        assert session.scalar(select(Outcome.outcome_id)) is None


@pytest.mark.asyncio
async def test_issue47_hard_decline_policy_recheck_blocks_a_legacy_approved_retry(app):
    with app.state.session_factory() as session:
        session.add(Customer(customer_id="cust_hard_1000", consent=True))
        session.commit()
    _seed_checkout_order(app, "order_hard_1000")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        failure = _payload(
            "payment.failed",
            payment_id="pay_hard_1000",
            order_id="order_hard_1000",
            customer_id="cust_hard_1000",
            event_id="event_hard_failed_1000",
            status="failed",
            error_code="HARD_DECLINE",
        )
        failed = await client.post(
            "/api/v1/webhooks/razorpay",
            content=failure,
            headers=_webhook_headers(failure, "event_hard_failed_1000"),
        )
        investigated = await client.post("/api/v1/cases/case_order_hard_1000/investigate")
        policy = await client.get("/api/v1/cases/case_order_hard_1000/policy")
        ranked = await client.get("/api/v1/cases/case_order_hard_1000/ranked-actions")

    with app.state.session_factory() as session:
        session.add(
            Decision(
                decision_id="legacy-hard-retry",
                case_id="case_order_hard_1000",
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
        action = await client.post(
            "/api/v1/cases/case_order_hard_1000/actions",
            json={"action": "retry", "idempotency_key": "hard-retry"},
        )
        audit = await client.get("/api/v1/audit/case_order_hard_1000")

    assert failed.status_code == 202
    assert investigated.json()["new_state"] == "eligible"
    assert policy.json()["blocked_reasons"] == {"retry": ["hard_decline"]}
    assert "retry" not in policy.json()["allowed_actions"]
    assert "retry" not in {item["action"] for item in ranked.json()["actions"]}
    assert action.status_code == 409
    assert action.json() == {"detail": ["hard_decline"]}
    assert app.state.provider_calls == []
    blocked = audit.json()[-1]
    assert blocked["event_type"] == "action.blocked"
    assert blocked["payload"]["reasons"] == ["hard_decline"]


@pytest.mark.asyncio
async def test_issue47_invalid_webhook_signature_has_no_persistence_side_effect(app):
    body = _payload(
        "payment.failed",
        payment_id="pay_invalid_1000",
        order_id="order_invalid_1000",
        customer_id="cust_invalid_1000",
        event_id="event_invalid_1000",
        status="failed",
        error_code="BAD_REQUEST_ERROR",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={
                "X-Razorpay-Event-Id": "event_invalid_1000",
                "X-Razorpay-Signature": "not-valid",
            },
        )

    assert response.status_code == 401
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PaymentEvent)) == 0
        assert session.scalar(select(func.count()).select_from(RecoveryCase)) == 0
        assert session.scalar(select(func.count()).select_from(Customer)) == 0
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0
