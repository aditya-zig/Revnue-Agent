from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.tables import AuditEvent, CheckoutOrder, Customer, PaymentEvent
from app.domain.enums import EvidenceSource
from app.domain.models import NormalizedPaymentEvent
from app.domain.state_machine import apply_event
from app.incidents.detector import detect_incidents


def record_event_and_update_case(session: Session, event: NormalizedPaymentEvent) -> bool:
    checkout_order = None
    if event.obligation_reference:
        checkout_order = session.scalar(
            select(CheckoutOrder).where(
                CheckoutOrder.provider_order_id == event.obligation_reference
            )
        )
        if checkout_order is not None and checkout_order.customer_id and not event.customer_id:
            # The provider order's persisted customer is authoritative when
            # webhook notes are absent or stale.
            event.customer_id = checkout_order.customer_id
    try:
        with session.begin_nested():
            session.add(PaymentEvent(**event.model_dump()))
            session.flush()
    except IntegrityError:
        return False
    if event.customer_id and session.get(Customer, event.customer_id) is None:
        # A webhook identifies the customer, but it does not prove contact consent.
        session.add(Customer(customer_id=event.customer_id, consent=False))
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
    if event.source_kind == EvidenceSource.RAZORPAY_TEST and event.authenticity_verified:
        session.flush()
        detect_incidents(session, as_of=event.occurred_at)
    return True
