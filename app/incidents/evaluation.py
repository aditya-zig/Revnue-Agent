"""Reproducible synthetic evaluation for the deterministic incident detector."""

from statistics import mean

from app.incidents.detector import (
    DETECTOR_VERSION,
    NONRECOVERABLE_ERROR_CODES,
    CohortMeasurement,
    measure_cohorts,
)
from simulator.merchant_day import generate_merchant_day

EVALUATION_SEEDS = (41, 43, 47, 53, 59)
CHECKPOINT_INTERVAL = 6


def run_detector_evaluation() -> dict[str, object]:
    planted_incidents = 0
    detected_incidents = 0
    true_positives = 0
    false_positives = 0
    correct_attributions = 0
    detection_latencies: list[float] = []
    affected_failed_events = 0
    affected_failed_value_paise = 0
    hard_decline_failed_value_paise = 0
    hard_decline_value_in_estimated_recoverable_paise = 0
    nonrecoverable_exclusion_violations = 0
    stable_traffic_false_positive_runs = 0
    false_positive_cost_proxy_paise = 0
    seed_results: list[dict[str, object]] = []

    for seed in EVALUATION_SEEDS:
        primary = generate_merchant_day(seed=seed, scenario="primary")
        planted = primary.planted_incidents[0]
        planted_incidents += 1
        expected_key = (planted.provider, planted.method)
        primary_detections: dict[tuple[str, str], CohortMeasurement] = {}
        first_expected: CohortMeasurement | None = None

        for checkpoint in range(
            CHECKPOINT_INTERVAL, len(primary.events) + 1, CHECKPOINT_INTERVAL
        ):
            for measurement in measure_cohorts(primary.events[:checkpoint]):
                expected_recoverable = sum(
                    event.amount
                    for event in measurement.current
                    if _failed(event)
                    and (event.error_code or "").upper()
                    not in NONRECOVERABLE_ERROR_CODES
                )
                leaked_nonrecoverable = max(
                    0,
                    measurement.recoverable_failed_value_paise
                    - expected_recoverable,
                )
                if leaked_nonrecoverable:
                    nonrecoverable_exclusion_violations += 1
                    hard_decline_value_in_estimated_recoverable_paise += (
                        leaked_nonrecoverable
                    )
                if not measurement.triggered:
                    continue
                key = (measurement.provider, measurement.method)
                primary_detections.setdefault(key, measurement)
                if key == expected_key and first_expected is None:
                    first_expected = measurement

        healthy = generate_merchant_day(seed=seed, scenario="healthy")
        healthy_detections: dict[tuple[str, str], CohortMeasurement] = {}
        for checkpoint in range(
            CHECKPOINT_INTERVAL, len(healthy.events) + 1, CHECKPOINT_INTERVAL
        ):
            for measurement in measure_cohorts(healthy.events[:checkpoint]):
                if measurement.triggered:
                    healthy_detections.setdefault(
                        (measurement.provider, measurement.method), measurement
                    )

        if healthy_detections:
            stable_traffic_false_positive_runs += 1
        detected_incidents += len(primary_detections) + len(healthy_detections)
        false_positives += len(healthy_detections)
        false_positive_cost_proxy_paise += sum(
            measurement.estimated_revenue_at_risk_paise
            for measurement in healthy_detections.values()
        )
        unexpected_primary = set(primary_detections) - {expected_key}
        false_positives += len(unexpected_primary)
        false_positive_cost_proxy_paise += sum(
            primary_detections[key].estimated_revenue_at_risk_paise
            for key in unexpected_primary
        )
        if expected_key in primary_detections:
            true_positives += 1
            correct_attributions += 1
        if first_expected is not None:
            detection_latencies.append(
                (first_expected.window_end - planted.starts_at).total_seconds()
            )

        planted_failures = [
            event
            for event in primary.events
            if event.provider == planted.provider
            and event.method == planted.method
            and planted.starts_at <= event.occurred_at <= planted.ends_at
            and _failed(event)
        ]
        affected_failed_events += len(planted_failures)
        affected_failed_value_paise += sum(event.amount for event in planted_failures)
        hard_declines = [
            event
            for event in primary.events
            if planted.starts_at <= event.occurred_at <= planted.ends_at
            and (event.error_code or "").upper() == "HARD_DECLINE"
        ]
        hard_decline_failed_value_paise += sum(event.amount for event in hard_declines)
        seed_results.append(
            {
                "seed": seed,
                "expected_cohort": {
                    "provider": planted.provider,
                    "method": planted.method,
                },
                "detected_cohorts": [
                    {"provider": provider, "method": method}
                    for provider, method in sorted(primary_detections)
                ],
                "healthy_false_positive_cohorts": [
                    {"provider": provider, "method": method}
                    for provider, method in sorted(healthy_detections)
                ],
                "detection_latency_seconds": (
                    (first_expected.window_end - planted.starts_at).total_seconds()
                    if first_expected is not None
                    else None
                ),
            }
        )

    false_negatives = max(0, planted_incidents - true_positives)
    precision = (
        true_positives / (true_positives + false_positives)
        if true_positives + false_positives
        else 0.0
    )
    recall = true_positives / planted_incidents if planted_incidents else 0.0
    attribution = correct_attributions / planted_incidents if planted_incidents else 0.0
    return {
        "claim": "SIMULATED",
        "detector_version": DETECTOR_VERSION,
        "seeds": list(EVALUATION_SEEDS),
        "stable_traffic_runs": len(EVALUATION_SEEDS),
        "planted_incidents": planted_incidents,
        "detected_incidents": detected_incidents,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": precision,
        "recall": recall,
        "cohort_attribution_accuracy": attribution,
        "mean_detection_latency_seconds": (
            mean(detection_latencies) if detection_latencies else 0.0
        ),
        "affected_failed_events": affected_failed_events,
        "affected_failed_value_paise": affected_failed_value_paise,
        "hard_decline_failed_value_paise": hard_decline_failed_value_paise,
        "hard_decline_value_in_estimated_recoverable_paise": (
            hard_decline_value_in_estimated_recoverable_paise
        ),
        "nonrecoverable_exclusion_violations": nonrecoverable_exclusion_violations,
        "stable_traffic_false_positive_runs": stable_traffic_false_positive_runs,
        "false_positive_cost_proxy_paise": false_positive_cost_proxy_paise,
        "seed_results": seed_results,
    }


def _failed(event: object) -> bool:
    status = getattr(event, "status", "")
    return str(getattr(status, "value", status)) == "failed"
