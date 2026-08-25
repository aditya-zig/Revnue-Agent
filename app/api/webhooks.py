import hashlib
import json

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.core.requests import read_limited_body
from app.core.security import verify_razorpay_signature
from app.db.tables import AuditEvent, RecoveryCase
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
        event = NormalizedPaymentEvent.from_razorpay(payload, hashlib.sha256(body).hexdigest())
        event.raw_body = body
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid webhook payload",
        ) from error

    factory = request.app.state.session_factory
    with factory() as session:
        if not record_event_and_update_case(session, event):
            if getattr(event, "obligation_reference", None):
                case = session.scalar(
                    select(RecoveryCase).where(
                        RecoveryCase.obligation_reference == event.obligation_reference
                    )
                )
                if case is None:
                    case = session.scalar(
                        select(RecoveryCase).where(RecoveryCase.payment_id == event.payment_id)
                    )
            else:
                case = session.scalar(
                    select(RecoveryCase).where(RecoveryCase.payment_id == event.payment_id)
                )
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
        session.commit()
    return {"event_id": event.event_id, "status": "accepted"}
