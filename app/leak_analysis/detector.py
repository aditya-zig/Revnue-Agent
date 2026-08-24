import re
from collections import defaultdict
from math import sqrt
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.tables import Customer, LeakFinding, Outcome, PaymentEvent, RecoveryCase

DETECTOR_VERSION = "leak-detector-v1"
MINIMUM_COHORT_SUPPORT = 3
DIMENSION_PRIORITY = {
    "error_reason": 0,
    "normalized_error_reason": 1,
    "error_code": 2,
    "error_step": 3,
    "error_source": 4,
    "method": 5,
    "customer_history": 6,
    "prior_successful_payments": 7,
    "amount_bucket": 8,
    "hour_bucket": 9,
    "day_bucket": 10,
    "failure_sequence": 11,
}


def detect_and_store_leaks(session: Session) -> list[LeakFinding]:
    session.execute(
        delete(LeakFinding).where(LeakFinding.detector_version == DETECTOR_VERSION)
    )
    rows: list[tuple[PaymentEvent, Customer | None]] = [
        (event, customer)
        for event, customer in session.execute(
            select(PaymentEvent, Customer)
            .outerjoin(Customer, Customer.customer_id == PaymentEvent.customer_id)
            .order_by(PaymentEvent.event_id)
        ).all()
    ]
    if not rows:
        return []

    cases = {case.payment_id: case for case in session.scalars(select(RecoveryCase))}
    outcomes = {outcome.case_id: outcome for outcome in session.scalars(select(Outcome))}
    default_recovery_probability = _calibrated_recovery_probability(list(outcomes.values()))
    baseline_rate = sum(event.status == "failed" for event, _ in rows) / len(rows)
    failure_sequence = _failure_sequence(rows)
    cohorts: dict[tuple[str, str], list[tuple[PaymentEvent, Customer | None]]] = defaultdict(list)

    for event, customer in rows:
        for dimension, value in _cohort_values(event, customer, failure_sequence[event.event_id]):
            cohorts[dimension, value].append((event, customer))

    findings: list[LeakFinding] = []
    for (dimension, value), cohort in cohorts.items():
        if len(cohort) < MINIMUM_COHORT_SUPPORT:
            continue
        finding = _create_finding(
            session,
            dimension,
            value,
            cohort,
            baseline_rate,
            cases,
            outcomes,
            default_recovery_probability,
        )
        if finding is not None:
            findings.append(finding)
    findings.sort(key=finding_sort_key)
    return findings


def finding_sort_key(finding: LeakFinding) -> tuple[int, float, int, str]:
    return (
        -finding.recoverable_impact,
        -finding.confidence,
        DIMENSION_PRIORITY[finding.cohort_filter["dimension"]],
        finding.cohort_filter["value"],
    )


def _failure_sequence(rows: list[tuple[PaymentEvent, Customer | None]]) -> dict[str, str]:
    failures_by_customer: dict[str, list[PaymentEvent]] = defaultdict(list)
    sequence = {event.event_id: "not_failed" for event, _ in rows}
    for event, customer in rows:
        if event.status == "failed":
            if customer is None:
                sequence[event.event_id] = "unknown"
                continue
            if customer.prior_failures > 0:
                sequence[event.event_id] = "repeated_failure"
                continue
            failures_by_customer[customer.customer_id].append(event)

    for failures in failures_by_customer.values():
        failures.sort(key=lambda event: (event.occurred_at, event.event_id))
        sequence[failures[0].event_id] = "first_failure"
        for event in failures[1:]:
            sequence[event.event_id] = "repeated_failure"
    return sequence


def _cohort_values(
    event: PaymentEvent, customer: Customer | None, failure_sequence: str
) -> list[tuple[str, str]]:
    return [
        ("method", event.method or "unknown"),
        ("error_reason", event.error_reason or "unknown"),
        ("normalized_error_reason", _normalize_error_reason(event.error_reason)),
        ("error_code", event.error_code or "unknown"),
        ("error_step", event.error_step or "unknown"),
        ("error_source", event.error_source or "unknown"),
        (
            "customer_history",
            "returning" if customer and customer.successful_payments > 0 else "new",
        ),
        ("amount_bucket", _amount_bucket(event.amount)),
        ("hour_bucket", _hour_bucket(event.occurred_at.hour)),
        ("day_bucket", event.occurred_at.strftime("%A").lower()),
        ("prior_successful_payments", _prior_successful_payments(customer)),
        ("failure_sequence", failure_sequence),
    ]


def _normalize_error_reason(error_reason: str | None) -> str:
    if not error_reason:
        return "unknown"
    return re.sub(r"[^a-z0-9]+", "_", error_reason.casefold()).strip("_") or "unknown"


def _amount_bucket(amount: int) -> str:
    if amount < 50_000:
        return "under_500_inr"
    if amount < 100_000:
        return "500_to_999_inr"
    return "1000_inr_or_more"


def _hour_bucket(hour: int) -> str:
    if hour < 6:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def _prior_successful_payments(customer: Customer | None) -> str:
    successful_payments = customer.successful_payments if customer else 0
    return str(successful_payments)


def _create_finding(
    session: Session,
    dimension: str,
    value: str,
    cohort: list[tuple[PaymentEvent, Customer | None]],
    baseline_rate: float,
    cases: dict[str, RecoveryCase],
    outcomes: dict[str, Outcome],
    default_recovery_probability: float,
) -> LeakFinding | None:
    failures = [event for event, _ in cohort if event.status == "failed"]
    observed_rate = len(failures) / len(cohort)
    if observed_rate <= baseline_rate:
        return None

    recoverable_cases = {
        case.payment_id: case
        for event in failures
        if (case := cases.get(event.payment_id)) is not None
        and case.state not in {"recovered", "stopped"}
    }
    attempted_value = sum(event.amount for event, _ in cohort)
    failed_value = sum(event.amount for event in failures)
    if failed_value == 0:
        return None
    unresolved_value = sum(case.amount_at_risk for case in recoverable_cases.values())
    impact = round((observed_rate - baseline_rate) * attempted_value)
    cohort_outcomes = [
        outcomes[case.case_id]
        for case in recoverable_cases.values()
        if case.case_id in outcomes
    ]
    recovery_probability = (
        _calibrated_recovery_probability(cohort_outcomes)
        if cohort_outcomes
        else default_recovery_probability
    )
    recoverable_impact = round(
        impact * unresolved_value / failed_value * recovery_probability
    )
    if recoverable_impact == 0:
        return None

    finding = LeakFinding(
        finding_id=f"finding_{uuid4().hex}",
        detector_version=DETECTOR_VERSION,
        cohort_filter={"dimension": dimension, "value": value},
        baseline_rate=baseline_rate,
        observed_rate=observed_rate,
        impact=impact,
        recoverable_impact=recoverable_impact,
        confidence=_wilson_lower_bound(len(failures), len(cohort)),
        evidence_json={
            "event_ids": [event.event_id for event, _ in cohort],
            "support": len(cohort),
            "failure_count": len(failures),
            "attempted_value": attempted_value,
            "failed_value": failed_value,
            "unresolved_value": unresolved_value,
            "recovery_probability": recovery_probability,
            "data_quality_warnings": _data_quality_warnings(dimension, value),
        },
    )
    session.add(finding)
    return finding


def _calibrated_recovery_probability(outcomes: list[Outcome]) -> float:
    if not outcomes:
        return 0.5
    return (sum(outcome.recovered for outcome in outcomes) + 1) / (len(outcomes) + 2)


def _wilson_lower_bound(successes: int, total: int) -> float:
    z = 1.96
    rate = successes / total
    denominator = 1 + z**2 / total
    centre = rate + z**2 / (2 * total)
    margin = z * sqrt((rate * (1 - rate) + z**2 / (4 * total)) / total)
    return (centre - margin) / denominator


def _data_quality_warnings(dimension: str, value: str) -> list[str]:
    if value == "unknown":
        return [f"{dimension} is missing for one or more events"]
    return []
