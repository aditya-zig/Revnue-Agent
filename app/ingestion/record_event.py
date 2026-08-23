from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.tables import PaymentEvent
from app.domain.models import NormalizedPaymentEvent
from app.domain.state_machine import apply_event


def record_event_and_update_case(session: Session, event: NormalizedPaymentEvent) -> bool:
    try:
        with session.begin_nested():
            session.add(PaymentEvent(**event.model_dump()))
            session.flush()
    except IntegrityError:
        return False
    apply_event(session, event)
    return True
