from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.db.tables import AuditEvent, PaymentException, RecoveryCase
from app.domain.models import PaymentExceptionRequest, PaymentExceptionResolutionRequest

router = APIRouter(prefix="/api/v1", tags=["payment exceptions"])
TERMINAL_CASE_STATES = {"recovered", "stopped", "escalated"}


def _role(request: Request) -> str:
    return request.headers.get("X-Reroute-Role", "operations_worker")


@router.get("/exceptions")
def list_payment_exceptions(request: Request) -> list[dict]:
    with request.app.state.session_factory() as session:
        exceptions = session.scalars(
            select(PaymentException).order_by(PaymentException.opened_at.desc())
        ).all()
        return [_exception_response(exception) for exception in exceptions]


@router.post("/cases/{case_id}/exceptions", status_code=status.HTTP_201_CREATED)
def open_payment_exception(
    case_id: str, payload: PaymentExceptionRequest, request: Request
) -> dict:
    with request.app.state.session_factory() as session:
        case = session.get(RecoveryCase, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        if case.state in TERMINAL_CASE_STATES:
            raise HTTPException(
                status_code=409, detail="terminal recovery case cannot open an exception"
            )
        existing = session.scalar(
            select(PaymentException).where(
                PaymentException.case_id == case.case_id,
                PaymentException.state == "open",
            )
        )
        if existing is not None:
            return _exception_response(existing)
        exception = PaymentException(
            exception_id=f"exception_{uuid4().hex}",
            case_id=case.case_id,
            kind=payload.kind,
            evidence_json=payload.evidence,
        )
        session.add(exception)
        session.add(
            AuditEvent(
                case_id=case.case_id,
                event_type="exception.opened",
                payload={"exception_id": exception.exception_id, "kind": exception.kind},
            )
        )
        session.commit()
        return _exception_response(exception)


@router.post("/exceptions/{exception_id}/resolve")
def resolve_payment_exception(
    exception_id: str, payload: PaymentExceptionResolutionRequest, request: Request
) -> dict:
    if _role(request) != "business_owner":
        raise HTTPException(status_code=403, detail="business owner role required")
    with request.app.state.session_factory() as session:
        exception = session.get(PaymentException, exception_id)
        if exception is None:
            raise HTTPException(status_code=404, detail="payment exception not found")
        if exception.state != "open":
            raise HTTPException(status_code=409, detail="payment exception is already resolved")
        case = session.get(RecoveryCase, exception.case_id)
        if case is None:
            raise HTTPException(status_code=409, detail="recovery case not found")
        if case.state in TERMINAL_CASE_STATES:
            raise HTTPException(
                status_code=409, detail="terminal recovery case cannot resolve an exception"
            )
        previous_state = case.state
        target_state = {
            "no_debit": "investigated",
            "reversed": "investigated",
            "captured": "recovered",
            "refunded": "stopped",
        }[payload.resolution]
        case.state = target_state
        if target_state == "stopped":
            case.stop_reason = "payment_exception_refunded"
        exception.state = "resolved"
        exception.resolution = payload.resolution
        exception.resolution_evidence_json = payload.evidence
        exception.resolved_at = datetime.now(UTC)
        session.add(
            AuditEvent(
                case_id=case.case_id,
                event_type=f"case.{target_state}",
                payload={"from": previous_state, "to": target_state},
            )
        )
        session.add(
            AuditEvent(
                case_id=case.case_id,
                event_type="exception.resolved",
                payload={
                    "exception_id": exception.exception_id,
                    "resolution": payload.resolution,
                },
            )
        )
        session.commit()
        return _exception_response(exception)


def _exception_response(exception: PaymentException) -> dict:
    return {
        "exception_id": exception.exception_id,
        "case_id": exception.case_id,
        "kind": exception.kind,
        "state": exception.state,
        "evidence": exception.evidence_json,
        "resolution": exception.resolution,
        "resolution_evidence": exception.resolution_evidence_json,
        "opened_at": exception.opened_at.isoformat(),
        "resolved_at": exception.resolved_at.isoformat() if exception.resolved_at else None,
    }
