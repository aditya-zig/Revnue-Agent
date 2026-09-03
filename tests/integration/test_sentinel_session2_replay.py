import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.tables import IncidentAuditEvent, PaymentEvent, PaymentIncident
from app.main import create_app

REPLAY_ID = "merchant_day_demo"
SEED = 47
TOTAL_EVENTS = 300
BASELINE_EVENTS = 150
INCIDENT_EVENTS = 90


@pytest.fixture
def app(database_url):
    return create_app(database_url=database_url, webhook_secret="test-secret")


def _signature(body: bytes) -> str:
    return hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_replay_establishes_healthy_baseline_then_opens_updates_and_resolves_one_incident(
    app,
):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        reset = await client.post("/api/v1/replay/reset", params={"replay_id": REPLAY_ID})
        baseline = await client.post(
            "/api/v1/replay/advance",
            params={"replay_id": REPLAY_ID, "seed": SEED, "count": BASELINE_EVENTS},
        )
        before = await client.get("/api/v1/incidents")
        incident_window = await client.post(
            "/api/v1/replay/advance",
            params={"replay_id": REPLAY_ID, "seed": SEED, "count": INCIDENT_EVENTS},
        )
        during = await client.get("/api/v1/incidents")
        detail = await client.get(f"/api/v1/incidents/{during.json()[0]['incident_id']}")
        incident_id = during.json()[0]["incident_id"]
        version_before = detail.json()["updated_at"]
        updated = await client.post(
            "/api/v1/replay/advance",
            params={"replay_id": REPLAY_ID, "seed": SEED, "count": 6},
        )
        detail_after_update = await client.get(f"/api/v1/incidents/{incident_id}")
        completed = await client.post(
            "/api/v1/replay/advance",
            params={"replay_id": REPLAY_ID, "seed": SEED, "count": TOTAL_EVENTS},
        )
        after = await client.get(f"/api/v1/incidents/{incident_id}")

    assert reset.status_code == 200
    assert reset.json()["claim"] == "SIMULATED"
    assert reset.json()["cursor"] == 0
    assert baseline.status_code == 201
    assert baseline.json()["cursor"] == BASELINE_EVENTS
    assert baseline.json()["stage"] == "baseline"
    assert before.status_code == 200
    assert before.json() == []

    assert incident_window.status_code == 201
    assert incident_window.json()["cursor"] == BASELINE_EVENTS + INCIDENT_EVENTS
    assert len(during.json()) == 1
    incident = during.json()[0]
    assert incident["state"] == "detected"
    assert incident["cohort_filter"]["provider"] == "simulated_psp_a"
    assert incident["cohort_filter"]["method"] == "upi"
    assert incident["provenance_summary"] == {"simulated_provider": 12}

    payload = detail.json()
    assert payload["detection_evidence"]["algorithm"] == "adjacent_two_proportion_z"
    assert payload["detection_evidence"]["claim_class"] == "ESTIMATED"
    assert payload["detection_evidence"]["root_cause_known"] is False
    assert payload["detection_evidence"]["direct_bank_or_npci_access"] is False
    drop = (
        payload["baseline_metrics"]["success_rate"]
        - payload["observed_metrics"]["success_rate"]
    )
    expected_risk = round(payload["observed_metrics"]["attempted_value_paise"] * drop)
    assert payload["estimated_amount_at_risk"] == expected_risk
    assert payload["detection_evidence"]["estimated_recoverable_paise"] <= expected_risk

    assert updated.status_code == 201
    assert len((await client.get("/api/v1/incidents")).json()) == 1
    assert detail_after_update.json()["updated_at"] != version_before

    assert completed.status_code == 201
    assert completed.json()["cursor"] == TOTAL_EVENTS
    assert after.json()["state"] == "resolved"
    assert after.json()["resolved_at"] is not None
    assert any(item["event_type"] == "incident.resolved" for item in after.json()["audit"])

    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PaymentIncident)) == 1
        assert session.scalar(select(func.count()).select_from(PaymentEvent)) == TOTAL_EVENTS
        assert session.scalar(
            select(func.count())
            .select_from(PaymentEvent)
            .where(PaymentEvent.source_kind != "simulated_provider")
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(PaymentEvent)
            .where(PaymentEvent.authenticity_verified.is_(True))
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(PaymentEvent)
            .where(PaymentEvent.error_code == "HARD_DECLINE")
        ) > 0
        assert session.scalar(select(func.count()).select_from(IncidentAuditEvent)) > 1


@pytest.mark.asyncio
async def test_replay_start_stops_at_first_incident_for_the_interactive_demo(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/replay/start",
            params={"replay_id": REPLAY_ID, "seed": SEED},
        )
        second = await client.post(
            "/api/v1/replay/start",
            params={"replay_id": REPLAY_ID, "seed": SEED},
        )
        incidents = await client.get("/api/v1/incidents")

    assert first.status_code == 201
    assert first.json()["claim"] == "SIMULATED"
    assert first.json()["state"] == "incident_detected"
    assert BASELINE_EVENTS < first.json()["cursor"] < BASELINE_EVENTS + INCIDENT_EVENTS
    assert first.json()["incident_id"] == incidents.json()[0]["incident_id"]
    assert second.status_code == 201
    assert second.json()["incident_id"] == first.json()["incident_id"]
    assert len(incidents.json()) == 1


@pytest.mark.asyncio
async def test_replay_seed_is_reproducible_and_healthy_scenario_has_no_false_positive(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/replay/run",
            params={"replay_id": REPLAY_ID, "seed": SEED, "scenario": "primary"},
        )
        with app.state.session_factory() as session:
            first_rows = [
                (event.event_id, event.amount, event.status, event.error_code, event.raw_hash)
                for event in session.scalars(
                    select(PaymentEvent).order_by(PaymentEvent.occurred_at, PaymentEvent.event_id)
                )
            ]
        second = await client.post(
            "/api/v1/replay/run",
            params={"replay_id": REPLAY_ID, "seed": SEED, "scenario": "primary"},
        )
        with app.state.session_factory() as session:
            second_rows = [
                (event.event_id, event.amount, event.status, event.error_code, event.raw_hash)
                for event in session.scalars(
                    select(PaymentEvent).order_by(PaymentEvent.occurred_at, PaymentEvent.event_id)
                )
            ]
        healthy = await client.post(
            "/api/v1/replay/run",
            params={"replay_id": REPLAY_ID, "seed": 53, "scenario": "healthy"},
        )
        healthy_incidents = await client.get("/api/v1/incidents")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first_rows == second_rows
    assert len(first_rows) == TOTAL_EVENTS
    assert healthy.status_code == 201
    assert healthy.json()["scenario"] == "healthy"
    assert healthy_incidents.json() == []


@pytest.mark.asyncio
async def test_detector_evaluation_is_reproducible_and_excludes_hard_declines(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/api/v1/evaluations/incident-detector")
        second = await client.get("/api/v1/evaluations/incident-detector")

    assert first.status_code == 200
    assert first.json() == second.json()
    payload = first.json()
    assert payload["claim"] == "SIMULATED"
    assert payload["detector_version"] == "sentinel-incident-v1"
    assert payload["seeds"] == [41, 43, 47, 53, 59]
    assert payload["planted_incidents"] == 5
    assert payload["detected_incidents"] == 5
    assert payload["true_positives"] == 5
    assert payload["false_positives"] == 0
    assert payload["precision"] == 1.0
    assert payload["recall"] == 1.0
    assert payload["cohort_attribution_accuracy"] == 1.0
    assert payload["mean_detection_latency_seconds"] > 0
    assert payload["affected_failed_events"] > 0
    assert payload["affected_failed_value_paise"] > 0
    assert payload["hard_decline_failed_value_paise"] > 0
    assert payload["hard_decline_value_in_estimated_recoverable_paise"] == 0


@pytest.mark.asyncio
async def test_signed_razorpay_webhook_remains_verified_test_mode_evidence(database_url):
    app = create_app(database_url=database_url, webhook_secret="test-secret")
    payload = {
        "id": "signed_session2_provider_event",
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_session2_provider",
                    "amount": 249900,
                    "currency": "INR",
                    "method": "upi",
                    "status": "failed",
                    "error_code": "GATEWAY_ERROR",
                    "error_reason": "technical_failure",
                    "created_at": 1788480000,
                    "notes": {"customer_id": "cust_session2_provider"},
                }
            }
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        accepted = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": _signature(body)},
        )
        duplicate = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": _signature(body)},
        )

    assert accepted.status_code == 202
    assert duplicate.status_code == 200
    with app.state.session_factory() as session:
        event = session.get(PaymentEvent, "evt_signed_session2_provider_event")
        assert event is not None
        assert event.provider == "razorpay_test"
        assert event.source_kind == "razorpay_test"
        assert event.authenticity_verified is True
        assert event.raw_hash == hashlib.sha256(body).hexdigest()
