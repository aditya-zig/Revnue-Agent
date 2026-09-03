import pytest
from httpx import ASGITransport, AsyncClient

from app.incidents.detector import NONRECOVERABLE_ERROR_CODES, measure_cohorts
from app.main import create_app
from simulator.merchant_day import generate_merchant_day

REPLAY_ID = "merchant_day_contract"
SEED = 47


@pytest.fixture
def app(database_url):
    return create_app(database_url=database_url, webhook_secret="test-secret")


@pytest.mark.asyncio
async def test_incident_contract_preserves_peak_detection_facts_through_resolution(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        started = await client.post(
            "/api/v1/replay/start",
            params={"replay_id": REPLAY_ID, "seed": SEED},
        )
        assert started.status_code == 201
        incident_id = started.json()["incident_id"]
        assert incident_id

        detected = await client.get(f"/api/v1/incidents/{incident_id}")
        assert detected.status_code == 200
        detected_payload = detected.json()
        detected_risk = detected_payload["estimated_amount_at_risk"]
        detected_confidence = detected_payload["confidence"]

        completed = await client.post(
            "/api/v1/replay/advance",
            params={"replay_id": REPLAY_ID, "seed": SEED, "count": 300},
        )
        assert completed.status_code == 201
        resolved = await client.get(f"/api/v1/incidents/{incident_id}")

    payload = resolved.json()
    assert payload["state"] == "resolved"
    assert payload["peak_estimated_amount_at_risk_paise"] >= detected_risk > 0
    assert payload["peak_confidence"] >= detected_confidence
    assert payload["provider"] == "simulated_psp_a"
    assert payload["method"] == "upi"
    assert payload["failed_attempt_count"] >= 1
    assert payload["linked_attempt_count"] >= payload["failed_attempt_count"]
    assert payload["amount_affected_paise"] >= payload["peak_estimated_amount_at_risk_paise"]
    assert (
        0
        <= payload["estimated_recoverable_paise"]
        <= payload["peak_estimated_amount_at_risk_paise"]
    )
    assert payload["resolution_reason"] == "signal_recovered_before_investigation"

    evidence = payload["detection_evidence"]
    trigger = evidence["trigger_snapshot"]
    assert trigger["baseline_success_rate"] > trigger["current_success_rate"]
    assert trigger["success_rate_drop"] >= 0.25
    assert trigger["z_score"] >= 1.96
    assert trigger["estimated_amount_at_risk_paise"] == detected_risk
    assert (
        evidence["peak_estimated_amount_at_risk_paise"]
        == payload["peak_estimated_amount_at_risk_paise"]
    )
    assert evidence["resolution_reason"] == payload["resolution_reason"]


def test_hard_declines_are_actually_excluded_from_recoverable_failure_value():
    day = generate_merchant_day(seed=SEED, scenario="primary")
    card = next(
        measurement
        for measurement in measure_cohorts(day.events[:180])
        if measurement.method == "card"
    )
    nonrecoverable_value = sum(
        event.amount
        for event in card.current
        if (event.error_code or "").upper() in NONRECOVERABLE_ERROR_CODES
    )
    recoverable_failure_value = sum(
        event.amount
        for event in card.current
        if str(getattr(event.status, "value", event.status)) == "failed"
        and (event.error_code or "").upper() not in NONRECOVERABLE_ERROR_CODES
    )

    assert nonrecoverable_value > 0
    assert card.failed_value_paise == nonrecoverable_value + recoverable_failure_value
    assert card.recoverable_failed_value_paise == recoverable_failure_value
    assert card.recoverable_failed_value_paise < card.failed_value_paise


@pytest.mark.asyncio
async def test_evaluation_reports_only_measured_reproducible_detector_metrics(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/evaluations/incident-detector")

    assert response.status_code == 200
    payload = response.json()
    assert payload["seeds"] == [41, 43, 47, 53, 59]
    assert payload["planted_incidents"] == 5
    assert payload["detected_incidents"] == 5
    assert payload["true_positives"] == 5
    assert payload["false_positives"] == 0
    assert payload["false_negatives"] == 0
    assert payload["precision"] == 1.0
    assert payload["recall"] == 1.0
    assert payload["cohort_attribution_accuracy"] == 1.0
    assert payload["mean_detection_latency_seconds"] == 1944.0
    assert payload["affected_failed_events"] == 104
    assert payload["affected_failed_value_paise"] == 21_939_600
    assert payload["hard_decline_failed_value_paise"] == 5_847_500
    assert payload["stable_traffic_runs"] == 5
    assert payload["stable_traffic_false_positive_runs"] == 0
    assert payload["nonrecoverable_exclusion_violations"] == 0
    assert "production_uplift" not in payload
