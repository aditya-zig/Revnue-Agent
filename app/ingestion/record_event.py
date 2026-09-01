from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.tables import AuditEvent, CheckoutOrder, Customer, PaymentEvent
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
    checkout_order = None
    if event.obligation_reference:
        checkout_order = session.scalar(
            select(CheckoutOrder).where(
                CheckoutOrder.provider_order_id == event.obligation_reference
            )
        )
    if checkout_order is not None:
        checkout_order.payment_id = event.payment_id
        checkout_order.status = f"payment_{event.status}"

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
