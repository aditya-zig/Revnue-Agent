from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.tables import AuditEvent, Customer, PaymentEvent
from app.domain.models import NormalizedPaymentEvent
from app.domain.state_machine import apply_event


def record_event_and_update_case(session: Session, event: NormalizedPaymentEvent) -> bool:
    try:
        with session.begin_nested():
            session.add(PaymentEvent(**event.model_dump()))
            session.flush()
    except IntegrityError:
        return False
    if event.customer_id and session.get(Customer, event.customer_id) is None:
        # A webhook identifies the customer, but it does not prove contact consent.
        session.add(Customer(customer_id=event.customer_id, consent=False))
    case_id = apply_event(session, event)
    if case_id:
        session.add(
            AuditEvent(
                case_id=case_id,
                event_type="event.recorded",
                payload={"event_id": event.event_id, "event_type": event.event_type},
            )
        )
    return True
