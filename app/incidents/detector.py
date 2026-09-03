"""Deterministic population-level incident detection for ReRoute Sentinel."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from math import sqrt
from statistics import NormalDist

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import (
    IncidentAuditEvent,
    PaymentEvent,
    PaymentIncident,
    RecoveryCase,
)
from app.domain.enums import EvidenceSource, IncidentState
from app.domain.incidents import link_case_to_incident, link_event_to_incident
from app.domain.models import NormalizedPaymentEvent

DETECTOR_VERSION = "sentinel-incident-v1"
ALGORITHM = "adjacent_two_proportion_z"
BASELINE_WINDOW = 24
CURRENT_WINDOW = 12
MIN_BASELINE_SUCCESS_RATE = 0.75
MAX_CURRENT_SUCCESS_RATE = 0.70
MIN_SUCCESS_RATE_DROP = 0.25
MIN_Z_SCORE = 1.96
RESTORED_SUCCESS_RATE = 0.75
HEALTHY_WINDOWS_TO_RESOLVE = 2
NONRECOVERABLE_ERROR_CODES = {
    "HARD_DECLINE",
    "CARD_EXPIRED",
    "CUSTOMER_CANCELLED",
}

Event = PaymentEvent | NormalizedPaymentEvent


@dataclass(frozen=True)
class CohortMeasurement:
    provider: str
    method: str
    source_kind: str
    baseline: tuple[Event, ...]
    current: tuple[Event, ...]
    baseline_success_rate: float
    current_success_rate: float
    success_rate_drop: float
    z_score: float
    confidence: float
    attempted_value_paise: int
    failed_value_paise: int
    recoverable_failed_value_paise: int
    estimated_revenue_at_risk_paise: int
    estimated_recoverable_paise: int
    affected_attempt_count: int
    window_end: datetime

    @property
    def triggered(self) -> bool:
        return (
            self.baseline_success_rate >= MIN_BASELINE_SUCCESS_RATE
            and self.current_success_rate <= MAX_CURRENT_SUCCESS_RATE
            and self.success_rate_drop >= MIN_SUCCESS_RATE_DROP
            and self.z_score >= MIN_Z_SCORE
        )


def measure_cohorts(events: Sequence[Event]) -> list[CohortMeasurement]:
    """Measure provider+method cohorts using adjacent historical/current windows."""

    cohorts: dict[tuple[str, str, str], list[Event]] = defaultdict(list)
    for event in sorted(events, key=lambda item: (_utc(item.occurred_at), item.event_id)):
        if not event.method:
            continue
        source_kind = _enum_value(event.source_kind)
        cohorts[(event.provider, event.method, source_kind)].append(event)

    measurements: list[CohortMeasurement] = []
    needed = BASELINE_WINDOW + CURRENT_WINDOW
    for (provider, method, source_kind), rows in sorted(cohorts.items()):
        if len(rows) < needed:
            continue
        baseline = tuple(rows[-needed:-CURRENT_WINDOW])
        current = tuple(rows[-CURRENT_WINDOW:])
        baseline_rate = _success_rate(baseline)
        current_rate = _success_rate(current)
        drop = max(0.0, baseline_rate - current_rate)
        z_score = _two_proportion_z(
            successes_a=sum(_is_success(event) for event in baseline),
            total_a=len(baseline),
            successes_b=sum(_is_success(event) for event in current),
            total_b=len(current),
        )
        attempted_value = sum(event.amount for event in current)
        failures = tuple(event for event in current if not _is_success(event))
        failed_value = sum(event.amount for event in failures)
        recoverable_failed_value = sum(
            event.amount
            for event in failures
            if (event.error_code or "").upper() not in NONRECOVERABLE_ERROR_CODES
        )
        risk = round(attempted_value * drop)
        measurements.append(
            CohortMeasurement(
                provider=provider,
                method=method,
                source_kind=source_kind,
                baseline=baseline,
                current=current,
                baseline_success_rate=baseline_rate,
                current_success_rate=current_rate,
                success_rate_drop=drop,
                z_score=z_score,
                confidence=NormalDist().cdf(z_score),
                attempted_value_paise=attempted_value,
                failed_value_paise=failed_value,
                recoverable_failed_value_paise=recoverable_failed_value,
                estimated_revenue_at_risk_paise=risk,
                estimated_recoverable_paise=min(risk, recoverable_failed_value),
                affected_attempt_count=round(len(current) * drop),
                window_end=_utc(current[-1].occurred_at),
            )
        )
    return measurements


def detect_incidents(
    session: Session,
    *,
    as_of: datetime | None = None,
    replay_id: str | None = None,
    seed: int | None = None,
    events: Sequence[Event] | None = None,
) -> list[PaymentIncident]:
    """Persist deterministic incident openings/updates for eligible evidence only."""

    eligible_events = (
        list(events)
        if events is not None
        else _load_events(
            session,
            as_of=as_of,
            replay_id=replay_id,
            seed=seed,
        )
    )
    if as_of is not None:
        cutoff = _utc(as_of)
        eligible_events = [
            event for event in eligible_events if _utc(event.occurred_at) <= cutoff
        ]
    measurements = measure_cohorts(eligible_events)
    touched: list[PaymentIncident] = []
    for measurement in measurements:
        if (
            replay_id is None
            and measurement.source_kind != EvidenceSource.RAZORPAY_TEST.value
        ):
            continue
        if (
            replay_id is not None
            and measurement.source_kind != EvidenceSource.SIMULATED_PROVIDER.value
        ):
            continue
        cohort_filter = _cohort_filter(measurement, replay_id=replay_id, seed=seed)
        incident = _find_active_incident(session, cohort_filter)
        if incident is None:
            if not measurement.triggered:
                continue
            incident = _open_incident(session, measurement, cohort_filter)
            touched.append(incident)
            continue
        if _measurement_already_applied(incident, measurement):
            continue
        _update_incident(session, incident, measurement)
        touched.append(incident)
    return touched


def triggered_measurements(events: Sequence[Event]) -> list[CohortMeasurement]:
    return [measurement for measurement in measure_cohorts(events) if measurement.triggered]


def _load_events(
    session: Session,
    *,
    as_of: datetime | None,
    replay_id: str | None,
    seed: int | None,
) -> list[PaymentEvent]:
    statement = select(PaymentEvent)
    if replay_id is None:
        statement = statement.where(
            PaymentEvent.source_kind == EvidenceSource.RAZORPAY_TEST.value,
            PaymentEvent.authenticity_verified.is_(True),
        )
    else:
        if seed is None:
            raise ValueError("seed is required for replay detection")
        prefix = f"evt_replay_{replay_id}_s{seed}_provider_event_%"
        statement = statement.where(
            PaymentEvent.source_kind == EvidenceSource.SIMULATED_PROVIDER.value,
            PaymentEvent.authenticity_verified.is_(False),
            PaymentEvent.event_id.like(prefix),
        )
    if as_of is not None:
        statement = statement.where(PaymentEvent.occurred_at <= as_of)
    return list(
        session.scalars(
            statement.order_by(PaymentEvent.occurred_at, PaymentEvent.event_id)
        ).all()
    )


def _cohort_filter(
    measurement: CohortMeasurement,
    *,
    replay_id: str | None,
    seed: int | None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "provider": measurement.provider,
        "method": measurement.method,
        "source_kind": measurement.source_kind,
    }
    if replay_id is not None:
        result["replay_id"] = replay_id
        result["seed"] = seed
    return result


def _find_active_incident(
    session: Session, cohort_filter: dict[str, object]
) -> PaymentIncident | None:
    candidates = session.scalars(
        select(PaymentIncident).where(
            PaymentIncident.detection_version == DETECTOR_VERSION,
            PaymentIncident.state != IncidentState.RESOLVED.value,
        )
    ).all()
    return next(
        (incident for incident in candidates if incident.cohort_filter == cohort_filter),
        None,
    )


def _open_incident(
    session: Session,
    measurement: CohortMeasurement,
    cohort_filter: dict[str, object],
) -> PaymentIncident:
    fingerprint = sha256(json_key(cohort_filter).encode()).hexdigest()[:12]
    incident_id = (
        f"incident_{fingerprint}_{measurement.window_end.strftime('%Y%m%d%H%M%S')}"
    )
    incident = PaymentIncident(
        incident_id=incident_id,
        state=IncidentState.DETECTED.value,
        opened_at=measurement.window_end,
        updated_at=measurement.window_end,
        resolved_at=None,
        detection_version=DETECTOR_VERSION,
        cohort_filter=cohort_filter,
        baseline_metrics=_baseline_metrics(measurement),
        observed_metrics=_observed_metrics(measurement),
        affected_attempt_count=measurement.affected_attempt_count,
        estimated_amount_at_risk=measurement.estimated_revenue_at_risk_paise,
        confidence=measurement.confidence,
        detection_evidence_json=_detection_evidence(measurement, healthy_streak=0),
        provenance_summary_json={measurement.source_kind: len(measurement.current)},
        analysis_reference=None,
        recommendation_reference=None,
    )
    session.add(incident)
    session.flush()
    session.add(
        IncidentAuditEvent(
            incident_id=incident.incident_id,
            event_type="incident.detected",
            payload={
                "detector_version": DETECTOR_VERSION,
                "algorithm": ALGORITHM,
                "cohort_filter": cohort_filter,
                "success_rate_drop": measurement.success_rate_drop,
                "z_score": measurement.z_score,
                "estimated_amount_at_risk": measurement.estimated_revenue_at_risk_paise,
                "claim_class": "ESTIMATED",
            },
            created_at=measurement.window_end,
        )
    )
    _link_measurement(session, incident, measurement)
    return incident


def _update_incident(
    session: Session,
    incident: PaymentIncident,
    measurement: CohortMeasurement,
) -> None:
    previous_evidence = incident.detection_evidence_json or {}
    healthy_streak = int(previous_evidence.get("healthy_window_streak", 0))
    if measurement.current_success_rate >= RESTORED_SUCCESS_RATE:
        healthy_streak += 1
    else:
        healthy_streak = 0

    incident.updated_at = measurement.window_end
    incident.baseline_metrics = _baseline_metrics(measurement)
    incident.observed_metrics = _observed_metrics(measurement)
    incident.affected_attempt_count = measurement.affected_attempt_count
    incident.estimated_amount_at_risk = measurement.estimated_revenue_at_risk_paise
    incident.confidence = measurement.confidence
    incident.detection_evidence_json = _detection_evidence(
        measurement,
        healthy_streak=healthy_streak,
    )
    incident.provenance_summary_json = {
        measurement.source_kind: len(measurement.current)
    }
    session.add(
        IncidentAuditEvent(
            incident_id=incident.incident_id,
            event_type="incident.updated",
            payload={
                "current_success_rate": measurement.current_success_rate,
                "success_rate_drop": measurement.success_rate_drop,
                "healthy_window_streak": healthy_streak,
                "window_end": measurement.window_end.isoformat(),
            },
            created_at=measurement.window_end,
        )
    )
    _link_measurement(session, incident, measurement)

    if (
        healthy_streak >= HEALTHY_WINDOWS_TO_RESOLVE
        and incident.state == IncidentState.DETECTED.value
    ):
        incident.state = IncidentState.RESOLVED.value
        incident.resolved_at = measurement.window_end
        session.add(
            IncidentAuditEvent(
                incident_id=incident.incident_id,
                event_type="incident.resolved",
                payload={
                    "from": IncidentState.DETECTED.value,
                    "to": IncidentState.RESOLVED.value,
                    "reason": "signal_recovered_before_investigation",
                    "healthy_window_streak": healthy_streak,
                    "restored_success_rate": measurement.current_success_rate,
                },
                created_at=measurement.window_end,
            )
        )


def _measurement_already_applied(
    incident: PaymentIncident, measurement: CohortMeasurement
) -> bool:
    evidence = incident.detection_evidence_json or {}
    current_window = evidence.get("current_window") or {}
    return current_window.get("end") == measurement.window_end.isoformat()


def _link_measurement(
    session: Session,
    incident: PaymentIncident,
    measurement: CohortMeasurement,
) -> None:
    for event in measurement.current:
        if session.get(PaymentEvent, event.event_id) is None:
            continue
        link_event_to_incident(session, incident.incident_id, event.event_id)
        if _is_success(event):
            continue
        case = session.scalar(
            select(RecoveryCase).where(
                RecoveryCase.obligation_reference == event.obligation_reference
            )
        )
        if case is not None:
            link_case_to_incident(session, incident.incident_id, case.case_id)


def _baseline_metrics(measurement: CohortMeasurement) -> dict[str, object]:
    return {
        "success_rate": measurement.baseline_success_rate,
        "attempts": len(measurement.baseline),
        "attempted_value_paise": sum(event.amount for event in measurement.baseline),
    }


def _observed_metrics(measurement: CohortMeasurement) -> dict[str, object]:
    return {
        "success_rate": measurement.current_success_rate,
        "attempts": len(measurement.current),
        "attempted_value_paise": measurement.attempted_value_paise,
        "failed_value_paise": measurement.failed_value_paise,
        "recoverable_failed_value_paise": measurement.recoverable_failed_value_paise,
    }


def _detection_evidence(
    measurement: CohortMeasurement,
    *,
    healthy_streak: int,
) -> dict[str, object]:
    return {
        "algorithm": ALGORITHM,
        "detector_version": DETECTOR_VERSION,
        "claim_class": "ESTIMATED",
        "root_cause_known": False,
        "direct_bank_or_npci_access": False,
        "baseline_window_size": BASELINE_WINDOW,
        "current_window_size": CURRENT_WINDOW,
        "minimum_baseline_success_rate": MIN_BASELINE_SUCCESS_RATE,
        "maximum_current_success_rate": MAX_CURRENT_SUCCESS_RATE,
        "minimum_success_rate_drop": MIN_SUCCESS_RATE_DROP,
        "minimum_z_score": MIN_Z_SCORE,
        "success_rate_drop": measurement.success_rate_drop,
        "z_score": measurement.z_score,
        "estimated_recoverable_paise": measurement.estimated_recoverable_paise,
        "nonrecoverable_error_codes": sorted(NONRECOVERABLE_ERROR_CODES),
        "healthy_window_streak": healthy_streak,
        "healthy_windows_required_to_resolve": HEALTHY_WINDOWS_TO_RESOLVE,
        "current_window": {
            "start": _utc(measurement.current[0].occurred_at).isoformat(),
            "end": measurement.window_end.isoformat(),
        },
        "baseline_window": {
            "start": _utc(measurement.baseline[0].occurred_at).isoformat(),
            "end": _utc(measurement.baseline[-1].occurred_at).isoformat(),
        },
    }


def _success_rate(events: Sequence[Event]) -> float:
    return sum(_is_success(event) for event in events) / len(events)


def _is_success(event: Event) -> bool:
    return _enum_value(event.status) == "captured"


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _two_proportion_z(
    *,
    successes_a: int,
    total_a: int,
    successes_b: int,
    total_b: int,
) -> float:
    rate_a = successes_a / total_a
    rate_b = successes_b / total_b
    pooled = (successes_a + successes_b) / (total_a + total_b)
    variance = pooled * (1 - pooled) * (1 / total_a + 1 / total_b)
    if variance <= 0:
        return 0.0
    return (rate_a - rate_b) / sqrt(variance)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def json_key(value: dict[str, object]) -> str:
    return "|".join(f"{key}={value[key]}" for key in sorted(value))
