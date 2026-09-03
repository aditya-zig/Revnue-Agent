"""Merchant-day replay controller built on the Session 1 incident contracts."""

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.db.replay import MerchantReplayControl
from app.db.tables import AuditEvent, Customer, PaymentEvent, PaymentIncident, RecoveryCase
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
ReplayState = Literal["reset", "running", "incident_detected", "complete"]


def replay_status(
    session: Session,
    *,
    replay_id: str = DEFAULT_REPLAY_ID,
    seed: int = DEFAULT_SEED,
) -> dict[str, object]:
    _validate_replay_id(replay_id)
    control = session.get(MerchantReplayControl, replay_id)
    if control is None:
        return _empty_payload(replay_id=replay_id, seed=seed)
    incident = _latest_replay_incident(session, control=control)
    return _payload(control=control, incident=incident)


def reset_replay(
    session: Session,
    *,
    replay_id: str = DEFAULT_REPLAY_ID,
) -> dict[str, object]:
    """Advance to a fresh replay generation without deleting historical evidence."""

    _validate_replay_id(replay_id)
    control = session.get(MerchantReplayControl, replay_id)
    generation = 1 if control is None else control.generation + 1
    if control is None:
        control = MerchantReplayControl(
            replay_id=replay_id,
            generation=generation,
            seed=DEFAULT_SEED,
            scenario="primary",
            cursor=0,
            state="reset",
            active_run_id=_run_id(replay_id, generation, DEFAULT_SEED, "primary"),
            updated_at=datetime.now(UTC),
        )
        session.add(control)
    else:
        control.generation = generation
        control.seed = DEFAULT_SEED
        control.scenario = "primary"
        control.cursor = 0
        control.state = "reset"
        control.active_run_id = _run_id(
            replay_id, generation, DEFAULT_SEED, "primary"
        )
        control.updated_at = datetime.now(UTC)
    session.flush()
    return {
        "claim": "SIMULATED",
        "replay_id": replay_id,
        "generation": generation,
        "run_id": control.active_run_id,
        "cursor": 0,
        "history_preserved": True,
        "state": "reset",
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
    control = _control_for_progress(
        session,
        replay_id=replay_id,
        seed=seed,
        scenario=scenario,
    )
    day = generate_merchant_day(
        seed=seed,
        replay_id=replay_id,
        scenario=scenario,
        run_id=control.active_run_id,
    )
    current = control.cursor
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
                run_id=control.active_run_id,
                events=day.events[:checkpoint],
            )
            session.flush()
        control.cursor = target
        control.updated_at = datetime.now(UTC)
        incident = _latest_replay_incident(session, control=control)
        control.state = _control_state(target, incident)
    else:
        incident = _latest_replay_incident(session, control=control)
    session.flush()
    return _payload(control=control, incident=incident)


def start_replay(
    session: Session,
    *,
    replay_id: str = DEFAULT_REPLAY_ID,
    seed: int = DEFAULT_SEED,
) -> dict[str, object]:
    """Replay only until Sentinel first detects the planted incident."""

    control = session.get(MerchantReplayControl, replay_id)
    if (
        control is not None
        and control.seed == seed
        and control.scenario == "primary"
        and control.state == "incident_detected"
    ):
        incident = _latest_replay_incident(session, control=control)
        if incident is not None:
            return _payload(control=control, incident=incident)

    if control is None:
        control = _create_control(
            session,
            replay_id=replay_id,
            seed=seed,
            scenario="primary",
        )
    elif control.cursor == 0:
        _configure_empty_control(control, seed=seed, scenario="primary")
    else:
        control = _next_generation(
            control,
            seed=seed,
            scenario="primary",
        )
    day = generate_merchant_day(
        seed=seed,
        replay_id=replay_id,
        scenario="primary",
        run_id=control.active_run_id,
    )
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
        run_id=control.active_run_id,
        events=day.events[:target],
    )
    session.flush()
    incident = touched[0] if touched else _latest_replay_incident(session, control=control)
    if incident is None:
        raise RuntimeError("detector did not persist the planted incident")
    control.cursor = target
    control.state = "incident_detected"
    control.updated_at = datetime.now(UTC)
    session.flush()
    return _payload(control=control, incident=incident)


def run_replay(
    session: Session,
    *,
    replay_id: str = DEFAULT_REPLAY_ID,
    seed: int = DEFAULT_SEED,
    scenario: ScenarioName = "primary",
) -> dict[str, object]:
    control = session.get(MerchantReplayControl, replay_id)
    if (
        control is not None
        and control.seed == seed
        and control.scenario == scenario
        and control.state == "complete"
        and control.cursor == TOTAL_EVENTS
    ):
        _verify_existing_prefix(
            session,
            generate_merchant_day(
                seed=seed,
                replay_id=replay_id,
                scenario=scenario,
                run_id=control.active_run_id,
            ),
            control.cursor,
        )
        return _payload(
            control=control,
            incident=_latest_replay_incident(session, control=control),
        )

    if control is None:
        control = _create_control(
            session,
            replay_id=replay_id,
            seed=seed,
            scenario=scenario,
        )
    elif control.cursor == 0:
        _configure_empty_control(control, seed=seed, scenario=scenario)
    else:
        control = _next_generation(control, seed=seed, scenario=scenario)

    day = generate_merchant_day(
        seed=seed,
        replay_id=replay_id,
        scenario=scenario,
        run_id=control.active_run_id,
    )
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
            run_id=control.active_run_id,
            events=day.events[:checkpoint],
        )
        session.flush()
    control.cursor = len(day.events)
    control.state = "complete"
    control.updated_at = datetime.now(UTC)
    incident = _latest_replay_incident(session, control=control)
    session.flush()
    return _payload(control=control, incident=incident)


def _create_control(
    session: Session,
    *,
    replay_id: str,
    seed: int,
    scenario: ScenarioName,
) -> MerchantReplayControl:
    _validate_replay_id(replay_id)
    control = MerchantReplayControl(
        replay_id=replay_id,
        generation=1,
        seed=seed,
        scenario=scenario,
        cursor=0,
        state="reset",
        active_run_id=_run_id(replay_id, 1, seed, scenario),
        updated_at=datetime.now(UTC),
    )
    session.add(control)
    session.flush()
    return control


def _next_generation(
    control: MerchantReplayControl,
    *,
    seed: int,
    scenario: ScenarioName,
) -> MerchantReplayControl:
    control.generation += 1
    control.seed = seed
    control.scenario = scenario
    control.cursor = 0
    control.state = "reset"
    control.active_run_id = _run_id(
        control.replay_id, control.generation, seed, scenario
    )
    control.updated_at = datetime.now(UTC)
    return control


def _configure_empty_control(
    control: MerchantReplayControl,
    *,
    seed: int,
    scenario: ScenarioName,
) -> None:
    control.seed = seed
    control.scenario = scenario
    control.active_run_id = _run_id(
        control.replay_id, control.generation, seed, scenario
    )
    control.state = "reset"
    control.updated_at = datetime.now(UTC)


def _control_for_progress(
    session: Session,
    *,
    replay_id: str,
    seed: int,
    scenario: ScenarioName,
) -> MerchantReplayControl:
    _validate_replay_id(replay_id)
    control = session.get(MerchantReplayControl, replay_id)
    if control is None:
        return _create_control(
            session,
            replay_id=replay_id,
            seed=seed,
            scenario=scenario,
        )
    if control.cursor == 0:
        _configure_empty_control(control, seed=seed, scenario=scenario)
        return control
    if control.seed != seed or control.scenario != scenario:
        raise ValueError(
            "active replay seed/scenario differs; reset or run a new generation"
        )
    return control


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
    prefix = f"evt_{day.run_id}_provider_event_%"
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
    control: MerchantReplayControl,
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
            if incident.cohort_filter.get("replay_id") == control.replay_id
            and incident.cohort_filter.get("seed") == control.seed
            and incident.cohort_filter.get("run_id") == control.active_run_id
        ),
        None,
    )


def _payload(
    *,
    control: MerchantReplayControl,
    incident: PaymentIncident | None,
) -> dict[str, object]:
    return {
        "claim": "SIMULATED",
        "replay_id": control.replay_id,
        "generation": control.generation,
        "run_id": control.active_run_id,
        "seed": control.seed,
        "scenario": control.scenario,
        "cursor": control.cursor,
        "total_events": TOTAL_EVENTS,
        "stage": _stage(control.cursor),
        "state": control.state,
        "incident_id": incident.incident_id if incident else None,
        "incident_state": incident.state if incident else None,
        "estimated_amount_at_risk": (
            incident.estimated_amount_at_risk if incident else 0
        ),
        "updated_at": incident.updated_at.isoformat() if incident else None,
        "history_preserved": True,
    }


def _empty_payload(*, replay_id: str, seed: int) -> dict[str, object]:
    return {
        "claim": "SIMULATED",
        "replay_id": replay_id,
        "generation": 0,
        "run_id": None,
        "seed": seed,
        "scenario": "primary",
        "cursor": 0,
        "total_events": TOTAL_EVENTS,
        "stage": "empty",
        "state": "reset",
        "incident_id": None,
        "incident_state": None,
        "estimated_amount_at_risk": 0,
        "updated_at": None,
        "history_preserved": True,
    }


def _run_id(
    replay_id: str,
    generation: int,
    seed: int,
    scenario: ScenarioName,
) -> str:
    return f"replay_{replay_id}_g{generation}_s{seed}_{scenario}"


def _control_state(
    cursor: int,
    incident: PaymentIncident | None,
) -> ReplayState:
    if cursor >= TOTAL_EVENTS:
        return "complete"
    if incident is not None:
        return "incident_detected"
    return "running"


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
