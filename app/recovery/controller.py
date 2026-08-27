import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from app.db.tables import Customer, Decision, RecoveryCase
from app.domain.models import DecisionResponse, StructuredDecision
from app.policy import evaluate_policy
from app.recovery.actions import execute_action
from app.recovery.scoring import RecoveryModel


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
    kill_switch: bool = False,
    contact_limit: int = 3,
    policy_version: str = "v1",
) -> tuple[DecisionResponse, bool]:
    decision_id = f"decision_{hashlib.sha256(idempotency_key.encode()).hexdigest()}"
    existing = session.get(Decision, decision_id)
    if existing is not None:
        if existing.case_id != case.case_id:
            raise ValueError("idempotency key belongs to another decision")
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
    if not scores:
        raise PermissionError(["no_allowed_action"])
    evidence = {
        "case": {
            "case_id": case.case_id,
            "amount_at_risk": case.amount_at_risk,
            "state": case.state,
        },
        "policy": policy.model_dump(),
        "scores": scores,
    }
    selected_action, selection_source, rejection = _select_action(
        evidence, policy.allowed_actions, decide_recovery_action
    )
    session.add(
        Decision(
            decision_id=decision_id,
            case_id=case.case_id,
            policy_version=policy.policy_version,
            model_version=recovery_model.report["model_version"],
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
            },
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
            model_version=recovery_model.report["model_version"],
            evidence=evidence,
            action=result,
        ),
        duplicate,
    )


def _select_action(
    evidence: dict,
    allowed_actions: list[str],
    decide_recovery_action: Callable[[dict], object] | None,
) -> tuple[str, Literal["model", "fallback"], str | None]:
    fallback = str(evidence["scores"][0]["action"])
    if decide_recovery_action is None:
        return fallback, "fallback", "model_unavailable"
    try:
        decision = StructuredDecision.model_validate(decide_recovery_action(evidence))
    except Exception as error:
        return fallback, "fallback", f"invalid_model_output: {error}"
    if decision.selected_action not in allowed_actions:
        return fallback, "fallback", "blocked_action"
    return decision.selected_action, "model", None
