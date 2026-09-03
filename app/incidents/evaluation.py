"""Reproducible synthetic evaluation for the deterministic incident detector."""

from statistics import mean

from app.incidents.detector import (
    DETECTOR_VERSION,
    NONRECOVERABLE_ERROR_CODES,
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
    seed_results: list[dict[str, object]] = []

    for seed in EVALUATION_SEEDS:
        primary = generate_merchant_day(seed=seed, scenario="primary")
        planted = primary.planted_incidents[0]
        planted_incidents += 1
        expected_key = (planted.provider, planted.method)
        primary_detections: dict[tuple[str, str], object] = {}
        first_expected = None

        for checkpoint in range(
            CHECKPOINT_INTERVAL, len(primary.events) + 1, CHECKPOINT_INTERVAL
        ):
            for measurement in measure_cohorts(primary.events[:checkpoint]):
                hard_current = sum(
                    event.amount
                    for event in measurement.current
                    if (event.error_code or "").upper()
                    in NONRECOVERABLE_ERROR_CODES
                )
                if hard_current:
                    nonrecoverable_excess = max(
                        0,
                        measurement.recoverable_failed_value_paise
                        - sum(
                            event.amount
                            for event in measurement.current
                            if _failed(event)
                            and (event.error_code or "").upper()
                            not in NONRECOVERABLE_ERROR_CODES
                        ),
                    )
                    hard_decline_value_in_estimated_recoverable_paise += (
                        nonrecoverable_excess
                    )
                if not measurement.triggered:
                    continue
                key = (measurement.provider, measurement.method)
                primary_detections.setdefault(key, measurement)
                if key == expected_key and first_expected is None:
                    first_expected = measurement

        healthy = generate_merchant_day(seed=seed, scenario="healthy")
        healthy_detections: dict[tuple[str, str], object] = {}
        for checkpoint in range(
            CHECKPOINT_INTERVAL, len(healthy.events) + 1, CHECKPOINT_INTERVAL
        ):
            for measurement in measure_cohorts(healthy.events[:checkpoint]):
                if measurement.triggered:
                    healthy_detections.setdefault(
                        (measurement.provider, measurement.method), measurement
                    )

        detected_incidents += len(primary_detections) + len(healthy_detections)
        false_positives += len(healthy_detections)
        unexpected_primary = set(primary_detections) - {expected_key}
        false_positives += len(unexpected_primary)
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
        "planted_incidents": planted_incidents,
        "detected_incidents": detected_incidents,
        "true_positives": true_positives,
        "false_positives": false_positives,
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
        "false_positive_cost_proxy_paise": 0,
        "seed_results": seed_results,
    }


def _failed(event: object) -> bool:
    status = getattr(event, "status", "")
    return str(getattr(status, "value", status)) == "failed"
