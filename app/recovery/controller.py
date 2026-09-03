import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from app.db.tables import AuditEvent, Customer, Decision, RecoveryCase
from app.domain.models import DecisionResponse, StructuredDecision
from app.policy import evaluate_policy
from app.recovery.actions import execute_action
from app.recovery.scoring import RecoveryModel

AI_RANKING_INPUT_VERSION = "policy-filtered-ranking-v1"


def _build_ai_ranking_input(
    policy_version: str,
    model_version: str,
    scores: list[dict[str, int | float | str]],
) -> dict:
    """Build the only action-ranking payload an external model may receive.

    Policy has already removed forbidden actions before this function is called.
    Blocked action names and reasons deliberately stay out of this payload so a
    model cannot restore an action that deterministic Policy removed.
    """
    return {
        "input_version": AI_RANKING_INPUT_VERSION,
        "policy_version": policy_version,
        "model_version": model_version,
        "candidate_actions": scores,
    }


def run_decision(
    session: Session,
    case: RecoveryCase,
    idempotency_key: str,
    now: datetime,
    quiet_hours_start: int,
    quiet_hours_end: int,
    create_payment_link: Callable[[int, str], str],
    recovery_model: RecoveryModel,
    decide_recovery_action: Callable[[dict], object] | None,
    approved: bool = False,
    requested_action: str | None = None,
    kill_switch: bool = False,
    contact_limit: int = 3,
    policy_version: str = "v1",
) -> tuple[DecisionResponse, bool]:
    decision_id = f"decision_{hashlib.sha256(idempotency_key.encode()).hexdigest()}"
    existing = session.get(Decision, decision_id)
    if existing is not None:
        if existing.case_id != case.case_id:
            raise ValueError("idempotency key belongs to another decision")
        if requested_action is not None and existing.selected_action != requested_action:
            raise ValueError("idempotency key belongs to another action")
        result = None
        if approved:
            approval = existing.reason_json.get("approval")
            already_granted = isinstance(approval, dict) and approval.get("granted") is True
            if not already_granted:
                existing.reason_json = {
                    **existing.reason_json,
                    "approval": {"required": True, "granted": True},
                }
                session.add(
                    AuditEvent(
                        case_id=case.case_id,
                        event_type="human.approval_granted",
                        payload={
                            "decision_id": existing.decision_id,
                            "selected_action": existing.selected_action,
                        },
                    )
                )
            result, _ = execute_action(
                session,
                case,
                existing.selected_action,
                idempotency_key,
                now,
                quiet_hours_start,
                quiet_hours_end,
                create_payment_link,
                kill_switch,
                contact_limit,
                policy_version,
            )
        reason = existing.reason_json
        return (
            DecisionResponse(
                decision_id=existing.decision_id,
                selected_action=existing.selected_action,
                selection_source=reason["selection_source"],
                policy_version=existing.policy_version,
                model_version=existing.model_version or "unknown",
                evidence=reason["evidence"],
                action=result,
            ),
            True,
        )

    policy = evaluate_policy(
        session,
        case,
        now,
        quiet_hours_start,
        quiet_hours_end,
        kill_switch,
        contact_limit,
        policy_version,
    )
    customer = session.get(Customer, case.customer_id) if case.customer_id else None
    scores = recovery_model.rank(case, customer, policy.allowed_actions)
    model_version = str(recovery_model.report["model_version"])
    ai_ranking_input = _build_ai_ranking_input(
        policy.policy_version,
        model_version,
        scores,
    )
    blocked_before_ai = [
        {
            "action": action,
            "reasons": reasons,
            "status": "removed_before_ai_ranking",
        }
        for action, reasons in policy.blocked_reasons.items()
    ]
    session.add(
        AuditEvent(
            case_id=case.case_id,
            event_type="policy.evaluated_before_ai_ranking",
            payload={
                "policy_version": policy.policy_version,
                "allowed_actions": policy.allowed_actions,
                "blocked_actions": blocked_before_ai,
                "ranking_input_version": AI_RANKING_INPUT_VERSION,
            },
        )
    )
    if not scores:
        session.commit()
        raise PermissionError(["no_allowed_action"])
    evidence = {
        "case": {
            "case_id": case.case_id,
            "amount_at_risk": case.amount_at_risk,
            "state": case.state,
        },
        "policy": policy.model_dump(),
        "scores": scores,
        "ai_ranking_input": ai_ranking_input,
        "blocked_before_ai_ranking": blocked_before_ai,
    }
    selection_source: Literal["model", "fallback"]
    if requested_action is not None:
        if requested_action not in policy.allowed_actions:
            session.commit()
            raise PermissionError(
                policy.blocked_reasons.get(requested_action, ["action_not_allowed"])
            )
        selected_action, selection_source, rejection = requested_action, "fallback", None
    else:
        selected_action, selection_source, rejection = _select_action(
            ai_ranking_input, decide_recovery_action
        )
    session.add(
        Decision(
            decision_id=decision_id,
            case_id=case.case_id,
            policy_version=policy.policy_version,
            model_version=model_version,
            allowed_actions=policy.allowed_actions,
            selected_action=selected_action,
            expected_value=next(
                int(score["expected_net_value"])
                for score in scores
                if score["action"] == selected_action
            ),
            reason_json={
                "evidence": evidence,
                "selection_source": selection_source,
                "rejection": rejection,
                "approval": {"required": True, "granted": approved},
            },
        )
    )
    if not approved:
        session.add(
            AuditEvent(
                case_id=case.case_id,
                event_type="human.approval_required",
                payload={"decision_id": decision_id, "selected_action": selected_action},
            )
        )
        session.commit()
        return (
            DecisionResponse(
                decision_id=decision_id,
                selected_action=selected_action,
                selection_source=selection_source,
                policy_version=policy.policy_version,
                model_version=model_version,
                evidence=evidence,
                action=None,
            ),
            False,
        )
    session.add(
        AuditEvent(
            case_id=case.case_id,
            event_type="human.approval_granted",
            payload={"decision_id": decision_id, "selected_action": selected_action},
        )
    )
    result, duplicate = execute_action(
        session,
        case,
        selected_action,
        idempotency_key,
        now,
        quiet_hours_start,
        quiet_hours_end,
        create_payment_link,
        kill_switch,
        contact_limit,
        policy_version,
    )
    return (
        DecisionResponse(
            decision_id=decision_id,
            selected_action=selected_action,
            selection_source=selection_source,
            policy_version=policy.policy_version,
            model_version=model_version,
            evidence=evidence,
            action=result,
        ),
        duplicate,
    )


def _select_action(
    ranking_input: dict,
    decide_recovery_action: Callable[[dict], object] | None,
) -> tuple[str, Literal["model", "fallback"], str | None]:
    candidate_actions = ranking_input["candidate_actions"]
    fallback = str(candidate_actions[0]["action"])
    if decide_recovery_action is None:
        return fallback, "fallback", "model_unavailable"
    try:
        decision = StructuredDecision.model_validate(decide_recovery_action(ranking_input))
    except Exception as error:
        return fallback, "fallback", f"invalid_model_output: {error}"
    allowed_actions = {str(candidate["action"]) for candidate in candidate_actions}
    if decision.selected_action not in allowed_actions:
        return fallback, "fallback", "blocked_action"
    return decision.selected_action, "model", None
