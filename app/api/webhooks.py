import hashlib
import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.core.requests import read_limited_body
from app.core.security import verify_razorpay_signature
from app.db.tables import ActionEvent, AuditEvent, PaymentException, RecoveryCase
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
