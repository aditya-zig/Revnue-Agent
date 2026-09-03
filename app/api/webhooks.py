import hashlib
import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.core.requests import read_limited_body
from app.core.security import verify_razorpay_signature
from app.db.tables import (
    ActionEvent,
    AuditEvent,
    CheckoutOrder,
    PaymentEvent,
    PaymentException,
    RecoveryCase,
)
from app.domain.models import NormalizedPaymentEvent
from app.ingestion.record_event import record_event_and_update_case

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


@router.post(
    "/razorpay",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=None,
)
async def receive_razorpay_webhook(request: Request) -> dict[str, str] | JSONResponse:
    body = await read_limited_body(request)
    if not verify_razorpay_signature(
        body=body,
        signature=request.headers.get("X-Razorpay-Signature"),
        secret=request.app.state.webhook_secret,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid webhook signature",
        )

    try:
        payload = json.loads(body)
        # The event ID header is not covered by Razorpay's HMAC and cannot be
        # allowed to change deduplication identity. The signed payload ID (or
        # raw-body hash) is normalized by from_razorpay.
        event = NormalizedPaymentEvent.from_razorpay(
            payload,
            hashlib.sha256(body).hexdigest(),
            authenticity_verified=True,
        )
        event.raw_body = body
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid webhook payload",
        ) from error

    factory = request.app.state.session_factory
    with factory() as session:
        # Check the signed identity before any payload-derived correlation. A
        # provider event ID is immutable evidence: a second signed body with
        # the same ID is a conflict, not a duplicate delivery.
        existing_event = session.scalar(
            select(PaymentEvent).where(
                PaymentEvent.provider_event_id == event.provider_event_id
            )
        )
        if existing_event is not None:
            if existing_event.raw_hash != event.raw_hash:
                return _conflict_response(session, existing_event, event)
            _correlate_recovery_payment(session, payload, event)
            return _duplicate_response(session, event)

        _correlate_recovery_payment(session, payload, event)
        _validate_checkout_order_terms(session, event)
        if not record_event_and_update_case(session, event):
            # Another worker may have inserted the event after the identity
            # check. Re-read the authoritative row before classifying it.
            existing_event = session.scalar(
                select(PaymentEvent).where(
                    PaymentEvent.provider_event_id == event.provider_event_id
                )
            )
            if existing_event is not None and existing_event.raw_hash != event.raw_hash:
                return _conflict_response(session, existing_event, event)
            return _duplicate_response(session, event)
        if event.error_reason == "payment_reversed":
            case = _case_for_event(session, event)
            if case is not None:
                _open_provider_reversal_exception(session, case, event)
        session.commit()
    return {"event_id": event.event_id, "status": "accepted"}


def _conflict_response(
    session, existing_event: PaymentEvent, event: NormalizedPaymentEvent
) -> JSONResponse:
    case = _case_for_identity(
        session, existing_event.payment_id, existing_event.obligation_reference
    )
    if case:
        session.add(
            AuditEvent(
                case_id=case.case_id,
                event_type="event.conflict",
                payload={
                    "event_id": existing_event.event_id,
                    "provider_event_id": existing_event.provider_event_id,
                },
            )
        )
        session.commit()
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": "provider event body conflicts with stored event"},
    )


def _duplicate_response(session, event: NormalizedPaymentEvent) -> JSONResponse:
    case = _case_for_event(session, event)
    if case:
        session.add(
            AuditEvent(
                case_id=case.case_id,
                event_type="event.duplicate",
                payload={"event_id": event.event_id, "event_type": event.event_type},
            )
        )
        session.commit()
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"event_id": event.event_id, "status": "duplicate"},
    )


def _validate_checkout_order_terms(session, event: NormalizedPaymentEvent) -> None:
    """Reject unowned or contradictory signed order-linked events.

    A storefront CheckoutOrder is the authoritative owner of its amount and
    currency. An unknown reference can never bootstrap a PaymentEvent or case
    from provider-supplied terms.
    """
    if not event.obligation_reference:
        return
    order = session.scalar(
        select(CheckoutOrder).where(
            CheckoutOrder.provider_order_id == event.obligation_reference
        )
    )
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="webhook order reference is not owned",
        )
    if event.amount != order.amount or event.currency != order.currency:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="webhook amount or currency does not match checkout order",
        )


def _correlate_recovery_payment(session, payload: dict, event: NormalizedPaymentEvent) -> None:
    """Resolve a recovery Payment Link through its persisted ActionEvent.

    Payment Link captures may omit ``order_id``. They are still attributable
    when the provider supplies the durable link ID persisted by a completed
    payment-link Action. Amount, Customer, and timing are intentionally not
    used as correlation keys.
    """
    if event.obligation_reference:
        return
    payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
    if not isinstance(payment, dict):
        return
    payment_link_id = payment.get("payment_link_id")
    if not isinstance(payment_link_id, str) or not payment_link_id:
        return
    # Only a completed payment-link action's durable provider ID can establish
    # this cross-attempt correlation. Customer-facing URLs, notes, and arbitrary
    # action keys are not authoritative provider references.
    action = session.scalar(
        select(ActionEvent).where(
            ActionEvent.tool == "payment_link",
            ActionEvent.status == "completed",
            ActionEvent.provider_reference_id == payment_link_id,
        )
    )
    if action is None:
        return
    case = session.get(RecoveryCase, action.case_id)
    if case is not None and case.obligation_reference:
        event.obligation_reference = case.obligation_reference
        event.merchant_order_reference = case.obligation_reference


def _case_for_identity(
    session, payment_id: str, obligation_reference: str | None
) -> RecoveryCase | None:
    if obligation_reference:
        case = session.scalar(
            select(RecoveryCase).where(
                RecoveryCase.obligation_reference == obligation_reference
            )
        )
        if case is not None:
            return case
        return session.scalar(
            select(RecoveryCase).where(
                RecoveryCase.payment_id == payment_id,
                RecoveryCase.obligation_reference.is_(None),
            )
        )
    return session.scalar(
        select(RecoveryCase).where(
            RecoveryCase.payment_id == payment_id,
            RecoveryCase.obligation_reference.is_(None),
        )
    )


def _case_for_event(session, event: NormalizedPaymentEvent) -> RecoveryCase | None:
    return _case_for_identity(session, event.payment_id, event.obligation_reference)


def _open_provider_reversal_exception(
    session, case: RecoveryCase, event: NormalizedPaymentEvent
) -> None:
    if case.state in {"recovered", "stopped", "escalated"}:
        return
    existing = session.scalar(
        select(PaymentException.exception_id).where(
            PaymentException.case_id == case.case_id,
            PaymentException.state == "open",
        )
    )
    if existing is not None:
        return
    exception = PaymentException(
        exception_id=f"exception_{uuid4().hex}",
        case_id=case.case_id,
        kind="provider_reversal",
        evidence_json={
            "event_id": event.event_id,
            "provider_event_id": event.provider_event_id,
            "error_reason": event.error_reason,
        },
    )
    session.add(exception)
    session.add(
        AuditEvent(
            case_id=case.case_id,
            event_type="exception.opened",
            payload={"exception_id": exception.exception_id, "kind": exception.kind},
        )
    )
