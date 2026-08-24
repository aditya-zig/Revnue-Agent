from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.db.tables import AuditEvent, RecoveryCase
from app.domain.models import PolicyResponse
from app.policy import evaluate_policy

router = APIRouter(prefix="/api/v1", tags=["cases"])


@router.get("/cases")
def list_cases(request: Request) -> list[dict]:
    with request.app.state.session_factory() as session:
        cases = session.scalars(select(RecoveryCase).order_by(RecoveryCase.case_id)).all()
        return [
            {
                "case_id": case.case_id,
                "customer_id": case.customer_id,
                "payment_id": case.payment_id,
                "amount_at_risk": case.amount_at_risk,
                "state": case.state,
                "attempts": case.attempts,
                "stop_reason": case.stop_reason,
            }
            for case in cases
        ]


@router.get("/audit/{case_id}")
def list_audit_events(case_id: str, request: Request) -> list[dict]:
    with request.app.state.session_factory() as session:
        case = session.get(RecoveryCase, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.case_id == case_id).order_by(AuditEvent.audit_id)
        ).all()
        return [
            {"case_id": event.case_id, "event_type": event.event_type, "payload": event.payload}
            for event in events
        ]


@router.get("/cases/{case_id}/policy")
def get_policy(case_id: str, request: Request) -> PolicyResponse:
    with request.app.state.session_factory() as session:
        case = session.get(RecoveryCase, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        return evaluate_policy(
            session,
            case,
            request.app.state.policy_now(),
            request.app.state.quiet_hours_start,
            request.app.state.quiet_hours_end,
        )
