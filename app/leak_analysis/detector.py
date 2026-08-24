from collections import defaultdict
from math import sqrt
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import Customer, LeakFinding, PaymentEvent, RecoveryCase

DETECTOR_VERSION = "leak-detector-v1"
MINIMUM_COHORT_SUPPORT = 3
DIMENSION_PRIORITY = {
    "error_reason": 0,
    "method": 1,
    "customer_history": 2,
    "amount_bucket": 3,
    "time_bucket": 4,
}


def detect_leaks(session: Session) -> list[LeakFinding]:
    rows = session.execute(
        select(PaymentEvent, Customer)
        .outerjoin(Customer, Customer.customer_id == PaymentEvent.customer_id)
        .order_by(PaymentEvent.event_id)
    ).all()
    if not rows:
        return []

    cases = {case.payment_id: case for case in session.scalars(select(RecoveryCase))}
    baseline_rate = sum(event.status == "failed" for event, _ in rows) / len(rows)
    cohorts: dict[tuple[str, str], list[tuple[PaymentEvent, Customer | None]]] = defaultdict(list)

    for event, customer in rows:
        for dimension, value in _cohort_values(event, customer):
            cohorts[dimension, value].append((event, customer))

    findings: list[LeakFinding] = []
    for (dimension, value), cohort in cohorts.items():
        if len(cohort) < MINIMUM_COHORT_SUPPORT:
            continue
        finding = _create_finding(session, dimension, value, cohort, baseline_rate, cases)
        if finding is not None:
            findings.append(finding)
    findings.sort(
        key=lambda finding: (
            -finding.impact,
            -finding.confidence,
            DIMENSION_PRIORITY[finding.cohort_filter["dimension"]],
            finding.cohort_filter["value"],
        )
    )
    return findings


def _cohort_values(event: PaymentEvent, customer: Customer | None) -> list[tuple[str, str]]:
    return [
        ("method", event.method or "unknown"),
        ("error_reason", event.error_reason or "unknown"),
        (
            "customer_history",
            "returning" if customer and customer.successful_payments > 0 else "new",
        ),
        ("amount_bucket", _amount_bucket(event.amount)),
        ("time_bucket", _time_bucket(event.occurred_at.hour)),
    ]


def _amount_bucket(amount: int) -> str:
    if amount < 50_000:
        return "under_500_inr"
    if amount < 100_000:
        return "500_to_999_inr"
    return "1000_inr_or_more"


def _time_bucket(hour: int) -> str:
    if hour < 6:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def _create_finding(
    session: Session,
    dimension: str,
    value: str,
    cohort: list[tuple[PaymentEvent, Customer | None]],
    baseline_rate: float,
    cases: dict[str, RecoveryCase],
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
    impact = sum(case.amount_at_risk for case in recoverable_cases.values())
    if impact == 0:
        return None

    finding = LeakFinding(
        finding_id=f"finding_{uuid4().hex}",
        detector_version=DETECTOR_VERSION,
        cohort_filter={"dimension": dimension, "value": value},
        baseline_rate=baseline_rate,
        observed_rate=observed_rate,
        impact=impact,
        confidence=_wilson_lower_bound(len(failures), len(cohort)),
        evidence_json={
            "event_ids": [event.event_id for event, _ in cohort],
            "support": len(cohort),
            "failure_count": len(failures),
            "data_quality_warnings": _data_quality_warnings(dimension, value),
        },
    )
    session.add(finding)
    return finding


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
