from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import ActionEvent, AuditEvent, Outcome, PaymentEvent, RecoveryCase
from app.domain.enums import CaseState, PaymentEventType
from app.domain.models import NormalizedPaymentEvent

TRANSITIONS = {
    CaseState.DETECTED: {CaseState.INVESTIGATED, CaseState.RECOVERED},
    CaseState.INVESTIGATED: {
        CaseState.ELIGIBLE,
        CaseState.STOPPED,
        CaseState.RECOVERED,
    },
    CaseState.ELIGIBLE: {
        CaseState.ACTION_SELECTED,
        CaseState.ESCALATED,
        CaseState.STOPPED,
        CaseState.RECOVERED,
    },
    CaseState.ACTION_SELECTED: {
        CaseState.AWAITING_OUTCOME,
        CaseState.ESCALATED,
        CaseState.RECOVERED,
    },
    CaseState.AWAITING_OUTCOME: {
        CaseState.ELIGIBLE,
        CaseState.RECOVERED,
        CaseState.ESCALATED,
        CaseState.STOPPED,
    },
    CaseState.ESCALATED: {CaseState.ELIGIBLE, CaseState.RECOVERED},
}


def transition_case(
    session: Session,
    case: RecoveryCase,
    target: CaseState,
    payload_extra: dict[str, object] | None = None,
) -> None:
    current = CaseState(case.state)
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError(f"cannot transition a {current} case to {target}")
    case.state = target
    payload: dict[str, object] = {"from": current, "to": target}
    if payload_extra:
        payload.update(payload_extra)
    session.add(
        AuditEvent(
            case_id=case.case_id,
            event_type=f"case.{target}",
            payload=payload,
        )
    )


def _obligation_case_id(event: NormalizedPaymentEvent) -> str:
    if event.obligation_reference:
        return f"case_{event.obligation_reference}"
    return f"case_{event.payment_id}"


def _find_case(session: Session, event: NormalizedPaymentEvent) -> RecoveryCase | None:
    if event.obligation_reference:
        case = session.scalar(
            select(RecoveryCase).where(
                RecoveryCase.obligation_reference == event.obligation_reference
            )
        )
        if case is not None:
            return case
        # fallback for legacy rows without obligation
        return session.scalar(
            select(RecoveryCase).where(
                RecoveryCase.payment_id == event.payment_id,
                RecoveryCase.obligation_reference.is_(None),
            )
        )
    # An event without a durable reference must not be attributed to an obligation case.
    return session.scalar(
        select(RecoveryCase).where(
            RecoveryCase.payment_id == event.payment_id,
            RecoveryCase.obligation_reference.is_(None),
        )
    )


def _matching_failure_exists(session: Session, case: RecoveryCase) -> bool:
    query = select(PaymentEvent.event_id).where(PaymentEvent.event_type == PaymentEventType.FAILED)
    if case.obligation_reference:
        query = query.where(PaymentEvent.obligation_reference == case.obligation_reference)
    else:
        query = query.where(PaymentEvent.payment_id == case.payment_id)
    return session.scalar(query) is not None


def apply_event(session: Session, event: NormalizedPaymentEvent) -> str | None:
    case = _find_case(session, event)
    if event.event_type == PaymentEventType.FAILED and case is None:
        # If a captured event already exists for this payment attempt, don't create a new case
        if event.obligation_reference:
            # captured for same obligation counts only if obligation matches
            captured_event = session.scalar(
                select(PaymentEvent.event_id).where(
                    PaymentEvent.obligation_reference == event.obligation_reference,
                    PaymentEvent.event_type == PaymentEventType.CAPTURED,
                )
            )
            if captured_event:
                return None
        else:
            captured_event = session.scalar(
                select(PaymentEvent.event_id).where(
                    PaymentEvent.payment_id == event.payment_id,
                    PaymentEvent.event_type == PaymentEventType.CAPTURED,
                )
            )
            if captured_event:
                return None
        case = RecoveryCase(
            case_id=_obligation_case_id(event),
            customer_id=event.customer_id,
            payment_id=event.payment_id,
            obligation_reference=event.obligation_reference,
            amount_at_risk=event.amount,
            state=CaseState.DETECTED,
            attempts=0,
        )
        session.add(case)
        detected_payload: dict[str, str | None] = {"payment_id": event.payment_id}
        if event.obligation_reference is not None:
            detected_payload["obligation_reference"] = event.obligation_reference
        session.add(
            AuditEvent(
                case_id=case.case_id,
                event_type="case.detected",
                payload=detected_payload,
            )
        )
        return case.case_id
    if event.event_type == PaymentEventType.CAPTURED and case:
        has_matching_failure = _matching_failure_exists(session, case)
        capture_matches_case = case.obligation_reference is None or (
            event.amount == case.amount_at_risk and has_matching_failure
        )
        if not capture_matches_case:
            return case.case_id
        if case.state == CaseState.STOPPED:
            return case.case_id
        if case.state != CaseState.RECOVERED:
            # Provider capture is authoritative and can arrive before the next planned transition.
            recovered_payload: dict[str, object] = {"payment_id": event.payment_id}
            if event.obligation_reference is not None:
                recovered_payload["obligation_reference"] = event.obligation_reference
            transition_case(
                session,
                case,
                CaseState.RECOVERED,
                payload_extra=recovered_payload,
            )
        pending_actions = session.scalars(
            select(ActionEvent).where(
                ActionEvent.case_id == case.case_id,
                ActionEvent.status == "pending",
            )
        ).all()
        for action in pending_actions:
            action.status = "cancelled"
            session.add(
                AuditEvent(
                    case_id=case.case_id,
                    event_type="action.cancelled",
                    payload={"action": action.tool, "idempotency_key": action.idempotency_key},
                )
            )
        if has_matching_failure and event.provider == "razorpay_test":
            outcome = session.scalar(select(Outcome).where(Outcome.case_id == case.case_id))
            if outcome is None:
                outcome = Outcome(
                    case_id=case.case_id,
                    recovered=True,
                    recovered_amount=event.amount,
                    contact_cost=0,
                    discount_cost=0,
                    resolved_at=event.occurred_at,
                    source="razorpay_test",
                )
                session.add(outcome)
                session.flush()
                session.add(
                    AuditEvent(
                        case_id=case.case_id,
                        event_type="outcome.recorded",
                        payload={
                            "event_id": event.event_id,
                            "provider_event_id": event.provider_event_id,
                            "payment_id": event.payment_id,
                            "obligation_reference": event.obligation_reference,
                            "amount": event.amount,
                            "occurred_at": event.occurred_at.isoformat(),
                            "source": "razorpay_test",
                        },
                    )
                )
            # Imported here to avoid a module cycle through recovery.actions -> state_machine.
            from app.incident_recovery import link_provider_outcome_to_incidents

            link_provider_outcome_to_incidents(
                session,
                case,
                outcome,
                event_id=event.event_id,
                provider_event_id=event.provider_event_id,
                payment_id=event.payment_id,
                amount=event.amount,
                source="razorpay_test",
            )
        return case.case_id
    return case.case_id if case else None
