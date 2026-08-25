from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from app.db.tables import AuditEvent, Customer, RecoveryCase
from app.domain.models import (
    ActionRequest,
    ActionResponse,
    DecisionRequest,
    DecisionResponse,
    PolicyResponse,
)
from app.policy import evaluate_policy
from app.recovery import execute_action, run_decision
from app.recovery.actions import ProviderError

router = APIRouter(prefix="/api/v1", tags=["cases"])


def _kill_switch(request: Request) -> bool:
    return bool(getattr(request.app.state, "kill_switch", False))


@router.get("/cases")
def list_cases(request: Request) -> list[dict]:
    with request.app.state.session_factory() as session:
        cases = session.scalars(select(RecoveryCase).order_by(RecoveryCase.case_id)).all()
        result = []
        for case in cases:
            payload: dict = {
                "case_id": case.case_id,
                "customer_id": case.customer_id,
                "payment_id": case.payment_id,
                "amount_at_risk": case.amount_at_risk,
                "state": case.state,
                "attempts": case.attempts,
                "stop_reason": case.stop_reason,
            }
            # include obligation only when verified, keep compatible
            obl = getattr(case, "obligation_reference", None)
            if obl is not None:
                payload["obligation_reference"] = obl
            result.append(payload)
        return result


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
            _kill_switch(request),
        )


@router.get("/cases/{case_id}/ranked-actions")
def get_ranked_actions(case_id: str, request: Request) -> dict:
    with request.app.state.session_factory() as session:
        case = session.get(RecoveryCase, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        policy = evaluate_policy(
            session,
            case,
            request.app.state.policy_now(),
            request.app.state.quiet_hours_start,
            request.app.state.quiet_hours_end,
            _kill_switch(request),
        )
        customer = session.get(Customer, case.customer_id) if case.customer_id else None
        return {
            "model_version": request.app.state.recovery_model.report["model_version"],
            "policy_version": policy.policy_version,
            "actions": request.app.state.recovery_model.rank(
                case, customer, policy.allowed_actions
            ),
        }


@router.post("/cases/{case_id}/actions", status_code=201)
def create_action(
    case_id: str, action: ActionRequest, request: Request, response: Response
) -> ActionResponse:
    with request.app.state.session_factory() as session:
        case = session.get(RecoveryCase, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        try:
            result, duplicate = execute_action(
                session,
                case,
                action.action,
                action.idempotency_key,
                request.app.state.policy_now(),
                request.app.state.quiet_hours_start,
                request.app.state.quiet_hours_end,
                request.app.state.create_payment_link,
                _kill_switch(request),
            )
        except PermissionError as error:
            raise HTTPException(status_code=409, detail=list(error.args[0])) from error
        except ProviderError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if duplicate:
            response.status_code = 200
        return result


@router.post("/cases/{case_id}/decisions", status_code=201)
def create_decision(
    case_id: str, decision: DecisionRequest, request: Request, response: Response
) -> DecisionResponse:
    with request.app.state.session_factory() as session:
        case = session.get(RecoveryCase, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        try:
            result, duplicate = run_decision(
                session,
                case,
                decision.idempotency_key,
                request.app.state.policy_now(),
                request.app.state.quiet_hours_start,
                request.app.state.quiet_hours_end,
                request.app.state.create_payment_link,
                request.app.state.recovery_model,
                request.app.state.decide_recovery_action,
                _kill_switch(request),
            )
        except PermissionError as error:
            raise HTTPException(status_code=409, detail=list(error.args[0])) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ProviderError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        if duplicate:
            response.status_code = 200
        return result
