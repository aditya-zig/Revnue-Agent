from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import ActionEvent, AuditEvent, PaymentEvent, RecoveryCase
from app.domain.enums import CaseState, PaymentEventType
from app.domain.models import NormalizedPaymentEvent

TRANSITIONS = {
    CaseState.DETECTED: {CaseState.INVESTIGATED},
    CaseState.INVESTIGATED: {CaseState.ELIGIBLE, CaseState.STOPPED},
    CaseState.ELIGIBLE: {CaseState.ACTION_SELECTED, CaseState.STOPPED},
    CaseState.ACTION_SELECTED: {CaseState.AWAITING_OUTCOME},
    CaseState.AWAITING_OUTCOME: {
        CaseState.ELIGIBLE,
        CaseState.RECOVERED,
        CaseState.ESCALATED,
        CaseState.STOPPED,
    },
}


def transition_case(session: Session, case: RecoveryCase, target: CaseState) -> None:
    current = CaseState(case.state)
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError(f"cannot transition a {current} case to {target}")
    case.state = target
    session.add(
        AuditEvent(
            case_id=case.case_id,
            event_type=f"case.{target}",
            payload={"from": current, "to": target},
        )
    )


def apply_event(session: Session, event: NormalizedPaymentEvent) -> str | None:
    case = session.scalar(select(RecoveryCase).where(RecoveryCase.payment_id == event.payment_id))
    if event.event_type == PaymentEventType.FAILED and case is None:
        captured_event = session.scalar(
            select(PaymentEvent.event_id).where(
                PaymentEvent.payment_id == event.payment_id,
                PaymentEvent.event_type == PaymentEventType.CAPTURED,
            )
        )
        if captured_event:
            return None
        case = RecoveryCase(
            case_id=f"case_{event.payment_id}",
            customer_id=event.customer_id,
            payment_id=event.payment_id,
            amount_at_risk=event.amount,
            state=CaseState.DETECTED,
            attempts=0,
        )
        session.add(case)
        session.add(
            AuditEvent(
                case_id=case.case_id,
                event_type="case.detected",
                payload={"payment_id": event.payment_id},
            )
        )
        return case.case_id
    if event.event_type == PaymentEventType.CAPTURED and case:
        if case.state not in {CaseState.RECOVERED, CaseState.ESCALATED, CaseState.STOPPED}:
            # Provider capture is authoritative and can arrive before the next planned transition.
            case.state = CaseState.RECOVERED
            session.add(
                AuditEvent(
                    case_id=case.case_id,
                    event_type="case.recovered",
                    payload={"payment_id": event.payment_id},
                )
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
        return case.case_id
    return case.case_id if case else None
