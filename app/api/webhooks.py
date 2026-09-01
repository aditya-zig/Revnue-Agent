import hashlib
import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.core.requests import read_limited_body
from app.core.security import verify_razorpay_signature
from app.db.tables import ActionEvent, AuditEvent, PaymentEvent, PaymentException, RecoveryCase
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
        event = NormalizedPaymentEvent.from_razorpay(
            payload,
            hashlib.sha256(body).hexdigest(),
            request.headers.get("X-Razorpay-Event-Id"),
        )
        event.raw_body = body
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid webhook payload",
        ) from error

    factory = request.app.state.session_factory
    with factory() as session:
        _correlate_recovery_payment(session, payload, event)
        if not record_event_and_update_case(session, event):
            existing_event = session.scalar(
                select(PaymentEvent).where(
                    PaymentEvent.provider_event_id == event.provider_event_id
                )
            )
            if existing_event is not None and existing_event.raw_hash != event.raw_hash:
                return JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content={"detail": "provider event body conflicts with stored event"},
                )
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
        if event.error_reason == "payment_reversed":
            case = _case_for_event(session, event)
            if case is not None:
                _open_provider_reversal_exception(session, case, event)
        session.commit()
    return {"event_id": event.event_id, "status": "accepted"}


def _correlate_recovery_payment(
    session, payload: dict, event: NormalizedPaymentEvent
) -> None:
    """Resolve a provider recovery-link payment to its original obligation.

    Payment Link captures can omit ``order_id``.  A persisted provider link
    reference is still an explicit correlation key; amount, customer, and time
    are deliberately not used as substitutes for PaymentObligation identity.
    """
    if event.obligation_reference:
        return
    payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
    if not isinstance(payment, dict):
        return
    notes = payment.get("notes")
    references = [
        payment.get("payment_link_id"),
        notes.get("reference_id") if isinstance(notes, dict) else None,
        notes.get("idempotency_key") if isinstance(notes, dict) else None,
    ]
    for reference in references:
        if not isinstance(reference, str) or not reference:
            continue
        action = session.scalar(
            select(ActionEvent).where(
                (ActionEvent.provider_reference == reference)
                | (ActionEvent.idempotency_key == reference)
            )
        )
        if action is None:
            continue
        case = session.get(RecoveryCase, action.case_id)
        if case is not None and case.obligation_reference:
            event.obligation_reference = case.obligation_reference
            return


def _case_for_event(session, event: NormalizedPaymentEvent) -> RecoveryCase | None:
    if event.obligation_reference:
        case = session.scalar(
            select(RecoveryCase).where(
                RecoveryCase.obligation_reference == event.obligation_reference
            )
        )
        if case is not None:
            return case
        return session.scalar(
            select(RecoveryCase).where(
                RecoveryCase.payment_id == event.payment_id,
                RecoveryCase.obligation_reference.is_(None),
            )
        )
    return session.scalar(
        select(RecoveryCase).where(
            RecoveryCase.payment_id == event.payment_id,
            RecoveryCase.obligation_reference.is_(None),
        )
    )


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
