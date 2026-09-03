"""Merchant-day replay controller built on the Session 1 incident contracts."""

from typing import Literal

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.db.tables import (
    ActionEvent,
    AuditEvent,
    Customer,
    Decision,
    IncidentAuditEvent,
    IncidentPaymentEvent,
    IncidentRecoveryCase,
    Outcome,
    PaymentEvent,
    PaymentException,
    PaymentIncident,
    RecoveryCase,
)
from app.domain.enums import CaseState, PaymentEventType
from app.domain.models import NormalizedPaymentEvent
from app.incidents.detector import DETECTOR_VERSION, detect_incidents, triggered_measurements
from simulator.merchant_day import (
    BASELINE_EVENTS,
    DEFAULT_REPLAY_ID,
    DEFAULT_SEED,
    INCIDENT_EVENTS,
    TOTAL_EVENTS,
    MerchantDay,
    ScenarioName,
    generate_merchant_day,
)

CHECKPOINT_INTERVAL = 6


def replay_status(
    session: Session,
    *,
    replay_id: str = DEFAULT_REPLAY_ID,
    seed: int = DEFAULT_SEED,
) -> dict[str, object]:
    _validate_replay_id(replay_id)
    cursor = _cursor(session, replay_id=replay_id, seed=seed)
    incident = _latest_replay_incident(session, replay_id=replay_id, seed=seed)
    return _payload(
        replay_id=replay_id,
        seed=seed,
        scenario="primary",
        cursor=cursor,
        incident=incident,
    )


def reset_replay(session: Session, *, replay_id: str = DEFAULT_REPLAY_ID) -> dict[str, object]:
    """Delete only data belonging to a named deterministic replay namespace."""

    _validate_replay_id(replay_id)
    prefix = f"evt_replay_{replay_id}_s%"
    events = list(
        session.scalars(
            select(PaymentEvent).where(PaymentEvent.event_id.like(prefix))
        ).all()
    )
    event_ids = [event.event_id for event in events]
    obligations = [
        event.obligation_reference for event in events if event.obligation_reference
    ]
    customer_ids = [event.customer_id for event in events if event.customer_id]
    cases: list[RecoveryCase] = []
    if obligations:
        cases = list(
            session.scalars(
                select(RecoveryCase).where(
                    RecoveryCase.obligation_reference.in_(obligations)
                )
            ).all()
        )
    case_ids = [case.case_id for case in cases]
    incidents = [
        incident
        for incident in session.scalars(
            select(PaymentIncident).where(
                PaymentIncident.detection_version == DETECTOR_VERSION
            )
        ).all()
        if incident.cohort_filter.get("replay_id") == replay_id
    ]
    incident_ids = [incident.incident_id for incident in incidents]

    if case_ids:
        session.execute(delete(Decision).where(Decision.case_id.in_(case_ids)))
        session.execute(delete(ActionEvent).where(ActionEvent.case_id.in_(case_ids)))
        session.execute(delete(Outcome).where(Outcome.case_id.in_(case_ids)))
        session.execute(
            delete(PaymentException).where(PaymentException.case_id.in_(case_ids))
        )
        session.execute(delete(AuditEvent).where(AuditEvent.case_id.in_(case_ids)))
        session.execute(
            delete(IncidentRecoveryCase).where(
                IncidentRecoveryCase.case_id.in_(case_ids)
            )
        )
        session.execute(delete(RecoveryCase).where(RecoveryCase.case_id.in_(case_ids)))
    if incident_ids:
        session.execute(
            delete(IncidentRecoveryCase).where(
                IncidentRecoveryCase.incident_id.in_(incident_ids)
            )
        )
        session.execute(
            delete(IncidentPaymentEvent).where(
                IncidentPaymentEvent.incident_id.in_(incident_ids)
            )
        )
        session.execute(
            delete(IncidentAuditEvent).where(
                IncidentAuditEvent.incident_id.in_(incident_ids)
            )
        )
        session.execute(
            delete(PaymentIncident).where(
                PaymentIncident.incident_id.in_(incident_ids)
            )
        )
    if event_ids:
        session.execute(
            delete(IncidentPaymentEvent).where(
                IncidentPaymentEvent.event_id.in_(event_ids)
            )
        )
        session.execute(delete(PaymentEvent).where(PaymentEvent.event_id.in_(event_ids)))
    if customer_ids:
        session.execute(delete(Customer).where(Customer.customer_id.in_(customer_ids)))
    session.flush()
    return {
        "claim": "SIMULATED",
        "replay_id": replay_id,
        "cursor": 0,
        "events_deleted": len(event_ids),
        "cases_deleted": len(case_ids),
        "incidents_deleted": len(incident_ids),
    }


def advance_replay(
    session: Session,
    *,
    replay_id: str = DEFAULT_REPLAY_ID,
    seed: int = DEFAULT_SEED,
    count: int,
    scenario: ScenarioName = "primary",
) -> dict[str, object]:
    if count < 1:
        raise ValueError("count must be positive")
    day = generate_merchant_day(seed=seed, replay_id=replay_id, scenario=scenario)
    current = _cursor(session, replay_id=replay_id, seed=seed)
    if current > len(day.events):
        raise ValueError("stored replay cursor exceeds deterministic merchant day")
    _verify_existing_prefix(session, day, current)
    target = min(len(day.events), current + count)
    if target > current:
        _bulk_insert_events(session, day.events[current:target])
        session.flush()
        for checkpoint in _checkpoints(current, target):
            detect_incidents(
                session,
                as_of=day.events[checkpoint - 1].occurred_at,
                replay_id=replay_id,
                seed=seed,
                events=day.events[:checkpoint],
            )
            session.flush()
    incident = _latest_replay_incident(session, replay_id=replay_id, seed=seed)
    return _payload(
        replay_id=replay_id,
        seed=seed,
        scenario=scenario,
        cursor=target,
        incident=incident,
    )


def start_replay(
    session: Session,
    *,
    replay_id: str = DEFAULT_REPLAY_ID,
    seed: int = DEFAULT_SEED,
) -> dict[str, object]:
    """Reset and replay only until Sentinel first detects the planted incident."""

    reset_replay(session, replay_id=replay_id)
    day = generate_merchant_day(seed=seed, replay_id=replay_id, scenario="primary")
    target = _first_detection_cursor(day)
    if target is None:
        raise RuntimeError("primary merchant-day fixture did not trigger the detector")
    _bulk_insert_events(session, day.events[:target])
    session.flush()
    touched = detect_incidents(
        session,
        as_of=day.events[target - 1].occurred_at,
        replay_id=replay_id,
        seed=seed,
        events=day.events[:target],
    )
    session.flush()
    incident = (
        touched[0]
        if touched
        else _latest_replay_incident(session, replay_id=replay_id, seed=seed)
    )
    if incident is None:
        raise RuntimeError("detector did not persist the planted incident")
    payload = _payload(
        replay_id=replay_id,
        seed=seed,
        scenario="primary",
        cursor=target,
        incident=incident,
    )
    payload["state"] = "incident_detected"
    return payload


def run_replay(
    session: Session,
    *,
    replay_id: str = DEFAULT_REPLAY_ID,
    seed: int = DEFAULT_SEED,
    scenario: ScenarioName = "primary",
) -> dict[str, object]:
    reset_replay(session, replay_id=replay_id)
    day = generate_merchant_day(seed=seed, replay_id=replay_id, scenario=scenario)
    _bulk_insert_events(session, day.events)
    session.flush()
    for checkpoint in range(
        CHECKPOINT_INTERVAL, len(day.events) + 1, CHECKPOINT_INTERVAL
    ):
        detect_incidents(
            session,
            as_of=day.events[checkpoint - 1].occurred_at,
            replay_id=replay_id,
            seed=seed,
            events=day.events[:checkpoint],
        )
        session.flush()
    incident = _latest_replay_incident(session, replay_id=replay_id, seed=seed)
    return _payload(
        replay_id=replay_id,
        seed=seed,
        scenario=scenario,
        cursor=len(day.events),
        incident=incident,
    )


def _bulk_insert_events(
    session: Session,
    events: tuple[NormalizedPaymentEvent, ...] | list[NormalizedPaymentEvent],
) -> None:
    if not events:
        return
    payment_rows = [event.model_dump(mode="python") for event in events]
    customer_rows = [
        {
            "customer_id": event.customer_id,
            "tenure_days": 0,
            "successful_payments": 0,
            "prior_failures": 0,
            "preferred_method": event.method,
            "consent": False,
            "locale": "en-IN",
        }
        for event in events
        if event.customer_id
    ]
    failed_events = [
        event for event in events if event.event_type == PaymentEventType.FAILED
    ]
    case_rows = [
        {
            "case_id": f"case_{event.obligation_reference or event.payment_id}",
            "customer_id": event.customer_id,
            "payment_id": event.payment_id,
            "obligation_reference": event.obligation_reference,
            "amount_at_risk": event.amount,
            "state": CaseState.DETECTED.value,
            "attempts": 0,
            "opened_at": event.occurred_at,
            "stop_reason": None,
        }
        for event in failed_events
    ]
    audit_rows: list[dict[str, object]] = []
    for event in failed_events:
        case_id = f"case_{event.obligation_reference or event.payment_id}"
        audit_rows.extend(
            [
                {
                    "case_id": case_id,
                    "event_type": "case.detected",
                    "payload": {
                        "payment_id": event.payment_id,
                        "obligation_reference": event.obligation_reference,
                    },
                    "created_at": event.occurred_at,
                },
                {
                    "case_id": case_id,
                    "event_type": "event.recorded",
                    "payload": {
                        "event_id": event.event_id,
                        "event_type": event.event_type.value,
                    },
                    "created_at": event.occurred_at,
                },
            ]
        )

    session.execute(insert(PaymentEvent), payment_rows)
    if customer_rows:
        session.execute(insert(Customer), customer_rows)
    if case_rows:
        session.execute(insert(RecoveryCase), case_rows)
    if audit_rows:
        session.execute(insert(AuditEvent), audit_rows)


def _verify_existing_prefix(session: Session, day: MerchantDay, cursor: int) -> None:
    if cursor == 0:
        return
    prefix = f"evt_replay_{day.replay_id}_s{day.seed}_provider_event_%"
    existing = list(
        session.scalars(
            select(PaymentEvent)
            .where(PaymentEvent.event_id.like(prefix))
            .order_by(PaymentEvent.occurred_at, PaymentEvent.event_id)
        ).all()
    )
    if len(existing) != cursor:
        raise ValueError("replay namespace contains a non-contiguous stored prefix")
    for stored, expected in zip(existing, day.events[:cursor], strict=True):
        if stored.raw_hash != expected.raw_hash:
            raise ValueError("stored replay data does not match the requested seed/scenario")


def _cursor(session: Session, *, replay_id: str, seed: int) -> int:
    prefix = f"evt_replay_{replay_id}_s{seed}_provider_event_%"
    return int(
        session.scalar(
            select(func.count())
            .select_from(PaymentEvent)
            .where(PaymentEvent.event_id.like(prefix))
        )
        or 0
    )


def _checkpoints(current: int, target: int) -> list[int]:
    checkpoints = [
        value
        for value in range(CHECKPOINT_INTERVAL, target + 1, CHECKPOINT_INTERVAL)
        if value > current
    ]
    if target > current and (not checkpoints or checkpoints[-1] != target):
        checkpoints.append(target)
    return checkpoints


def _first_detection_cursor(day: MerchantDay) -> int | None:
    for checkpoint in range(
        CHECKPOINT_INTERVAL, len(day.events) + 1, CHECKPOINT_INTERVAL
    ):
        triggered = triggered_measurements(day.events[:checkpoint])
        if any(
            measurement.provider == day.planted_incidents[0].provider
            and measurement.method == day.planted_incidents[0].method
            for measurement in triggered
        ):
            return checkpoint
    return None


def _latest_replay_incident(
    session: Session,
    *,
    replay_id: str,
    seed: int,
) -> PaymentIncident | None:
    candidates = session.scalars(
        select(PaymentIncident)
        .where(PaymentIncident.detection_version == DETECTOR_VERSION)
        .order_by(PaymentIncident.opened_at.desc())
    ).all()
    return next(
        (
            incident
            for incident in candidates
            if incident.cohort_filter.get("replay_id") == replay_id
            and incident.cohort_filter.get("seed") == seed
        ),
        None,
    )


def _payload(
    *,
    replay_id: str,
    seed: int,
    scenario: ScenarioName,
    cursor: int,
    incident: PaymentIncident | None,
) -> dict[str, object]:
    return {
        "claim": "SIMULATED",
        "replay_id": replay_id,
        "seed": seed,
        "scenario": scenario,
        "cursor": cursor,
        "total_events": TOTAL_EVENTS,
        "stage": _stage(cursor),
        "incident_id": incident.incident_id if incident else None,
        "incident_state": incident.state if incident else None,
        "estimated_amount_at_risk": (
            incident.estimated_amount_at_risk if incident else 0
        ),
        "updated_at": incident.updated_at.isoformat() if incident else None,
    }


def _validate_replay_id(replay_id: str) -> None:
    if not replay_id or not replay_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError(
            "replay_id must contain only letters, numbers, hyphens, or underscores"
        )


def _stage(
    cursor: int,
) -> Literal["empty", "baseline", "incident", "recovery", "complete"]:
    if cursor <= 0:
        return "empty"
    if cursor <= BASELINE_EVENTS:
        return "baseline"
    if cursor <= BASELINE_EVENTS + INCIDENT_EVENTS:
        return "incident"
    if cursor < TOTAL_EVENTS:
        return "recovery"
    return "complete"
