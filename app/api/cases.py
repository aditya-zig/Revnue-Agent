from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from app.db.tables import AuditEvent, Customer, RecoveryCase
from app.domain.enums import CaseState
from app.domain.models import (
    ActionRequest,
    ActionResponse,
    DecisionRequest,
    DecisionResponse,
    PolicyResponse,
)
from app.domain.state_machine import transition_case
from app.policy import evaluate_policy, get_policy_configuration
from app.recovery import execute_action, run_decision
from app.recovery.actions import ProviderError

router = APIRouter(prefix="/api/v1", tags=["cases"])


def _policy_configuration(session, request: Request):
    return get_policy_configuration(session, request.app.state)


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
        configuration = _policy_configuration(session, request)
        return evaluate_policy(
            session,
            case,
            request.app.state.policy_now(),
            configuration.quiet_hours_start,
            configuration.quiet_hours_end,
            configuration.kill_switch,
            configuration.contact_limit,
            configuration.policy_version,
        )


@router.get("/cases/{case_id}/ranked-actions")
def get_ranked_actions(case_id: str, request: Request) -> dict:
    with request.app.state.session_factory() as session:
        case = session.get(RecoveryCase, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        configuration = _policy_configuration(session, request)
        policy = evaluate_policy(
            session,
            case,
            request.app.state.policy_now(),
            configuration.quiet_hours_start,
            configuration.quiet_hours_end,
            configuration.kill_switch,
            configuration.contact_limit,
            configuration.policy_version,
        )
        customer = session.get(Customer, case.customer_id) if case.customer_id else None
        return {
            "model_version": request.app.state.recovery_model.report["model_version"],
            "policy_version": policy.policy_version,
            "actions": request.app.state.recovery_model.rank(
                case, customer, policy.allowed_actions
            ),
        }


@router.post("/cases/{case_id}/investigate", status_code=200)
def investigate_case(case_id: str, request: Request) -> dict:
    with request.app.state.session_factory() as session:
        case = session.get(RecoveryCase, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        if case.state != CaseState.DETECTED:
            raise HTTPException(status_code=409, detail="case is not detected")

        transition_case(session, case, CaseState.INVESTIGATED)
        configuration = _policy_configuration(session, request)
        policy = evaluate_policy(
            session,
            case,
            request.app.state.policy_now(),
            configuration.quiet_hours_start,
            configuration.quiet_hours_end,
            configuration.kill_switch,
            configuration.contact_limit,
            configuration.policy_version,
        )
        if policy.allowed_actions:
            transition_case(
                session,
                case,
                CaseState.ELIGIBLE,
                payload_extra={
                    "policy_version": policy.policy_version,
                    "allowed_actions": policy.allowed_actions,
                },
            )
        session.commit()
        return {
            "case_id": case.case_id,
            "new_state": case.state,
            "policy": policy.model_dump(),
        }


@router.post("/cases/{case_id}/resume", status_code=200)
def resume_case(case_id: str, request: Request) -> dict[str, str]:
    if request.headers.get("X-Reroute-Role", "operations_worker") != "business_owner":
        raise HTTPException(status_code=403, detail="business owner role required")
    with request.app.state.session_factory() as session:
        case = session.get(RecoveryCase, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        if case.state != CaseState.ESCALATED:
            raise HTTPException(status_code=409, detail="case is not awaiting approval")
        transition_case(session, case, CaseState.ELIGIBLE)
        session.add(
            AuditEvent(
                case_id=case.case_id,
                event_type="human.approval_granted",
                payload={"actor_role": "business_owner", "from": "escalated", "to": "eligible"},
            )
        )
        session.commit()
        return {"case_id": case_id, "new_state": case.state}


@router.post("/cases/{case_id}/actions", status_code=201)
def create_action(
    case_id: str, action: ActionRequest, request: Request, response: Response
) -> ActionResponse:
    with request.app.state.session_factory() as session:
        case = session.get(RecoveryCase, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        configuration = _policy_configuration(session, request)
        try:
            result, duplicate = execute_action(
                session,
                case,
                action.action,
                action.idempotency_key,
                request.app.state.policy_now(),
                configuration.quiet_hours_start,
                configuration.quiet_hours_end,
                request.app.state.create_payment_link,
                configuration.kill_switch,
                configuration.contact_limit,
                configuration.policy_version,
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
        configuration = _policy_configuration(session, request)
        try:
            result, duplicate = run_decision(
                session,
                case,
                decision.idempotency_key,
                request.app.state.policy_now(),
                configuration.quiet_hours_start,
                configuration.quiet_hours_end,
                request.app.state.create_payment_link,
                request.app.state.recovery_model,
                request.app.state.decide_recovery_action,
                decision.approved,
                configuration.kill_switch,
                configuration.contact_limit,
                configuration.policy_version,
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
