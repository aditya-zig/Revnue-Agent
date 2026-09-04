"""Hardened Sentinel incident recommendation, approval, execution and read model."""

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import (
    ActionEvent,
    Customer,
    Decision,
    IncidentAuditEvent,
    IncidentRecoveryCase,
    Outcome,
    PaymentIncident,
    RecoveryCase,
)
from app.domain.enums import CaseState, ClaimTag, EvidenceSource, IncidentState
from app.domain.incidents import transition_incident
from app.domain.models import ActionResponse
from app.domain.state_machine import transition_case
from app.incident_analysis import read_incident_analysis
from app.policy import evaluate_policy, get_policy_configuration
from app.recovery.actions import ProviderError, execute_action
from app.recovery.scoring import RecoveryModel

RECOMMENDATION_VERSION = "sentinel-recommendation-v2"
RECOMMENDATION_EVENT = "incident.recommendation.ready"
APPROVAL_EVENT = "incident.approval.granted"
EXECUTION_EVENT = "incident.execution.completed"
EXECUTION_FAILED_EVENT = "incident.execution.failed"
STALE_EXECUTION_EVENT = "incident.execution.blocked_stale_context"


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _execution_key(recommendation_id: str) -> str:
    return f"incident-exec:{recommendation_id}"


def _decision_id(execution_key: str) -> str:
    return f"decision_{hashlib.sha256(execution_key.encode()).hexdigest()}"


def _confidence_label(probability: float) -> str:
    if probability >= 0.75:
        return "high"
    if probability >= 0.45:
        return "medium"
    return "low"


def _action_claim_tag(action: str, payment_link_test_mode: bool) -> str:
    if action == "payment_link":
        return ClaimTag.TEST_MODE.value if payment_link_test_mode else ClaimTag.MOCK.value
    if action == "retry":
        return ClaimTag.SIMULATED.value
    return ClaimTag.MOCK.value


def _prepare_case_for_recommendation(
    session: Session,
    case: RecoveryCase,
    now: datetime,
    app_state: Any,
) -> None:
    if case.state == CaseState.DETECTED:
        transition_case(session, case, CaseState.INVESTIGATED)
    if case.state != CaseState.INVESTIGATED:
        return
    configuration = get_policy_configuration(session, app_state)
    policy = evaluate_policy(
        session,
        case,
        now,
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
                "source": "incident_control",
            },
        )


def _case_policy_context(
    session: Session,
    case: RecoveryCase,
    now: datetime,
    app_state: Any,
) -> tuple[dict[str, Any], Any]:
    configuration = get_policy_configuration(session, app_state)
    policy = evaluate_policy(
        session,
        case,
        now,
        configuration.quiet_hours_start,
        configuration.quiet_hours_end,
        configuration.kill_switch,
        configuration.contact_limit,
        configuration.policy_version,
    )
    customer = session.get(Customer, case.customer_id) if case.customer_id else None
    context = {
        "case_id": case.case_id,
        "case_state": str(case.state),
        "amount_at_risk_paise": case.amount_at_risk,
        "attempts": case.attempts,
        "customer_id_present": case.customer_id is not None,
        "customer_consent": customer.consent if customer is not None else None,
        "policy_version": policy.policy_version,
        "allowed_actions": list(policy.allowed_actions),
        "blocked_reasons": policy.blocked_reasons,
        "kill_switch": configuration.kill_switch,
        "contact_limit": configuration.contact_limit,
        "quiet_hours_start": configuration.quiet_hours_start,
        "quiet_hours_end": configuration.quiet_hours_end,
    }
    return context, policy


def build_case_recommendation(
    session: Session,
    case: RecoveryCase,
    now: datetime,
    app_state: Any,
    recovery_model: RecoveryModel,
    payment_link_test_mode: bool,
) -> dict[str, Any]:
    """Run deterministic Policy first, then rank only its allowed actions."""

    _prepare_case_for_recommendation(session, case, now, app_state)
    context, policy = _case_policy_context(session, case, now, app_state)
    customer = session.get(Customer, case.customer_id) if case.customer_id else None
    scores = recovery_model.rank(case, customer, policy.allowed_actions)
    alternatives = [
        {
            **score,
            "confidence_label": _confidence_label(float(score["recovery_probability"])),
            "claim_tag": _action_claim_tag(str(score["action"]), payment_link_test_mode),
        }
        for score in scores
    ]
    top = alternatives[0] if alternatives else None
    return {
        "case_id": case.case_id,
        "case_state": case.state,
        "context_hash": _canonical_hash(context),
        "deterministic_context": context,
        "recommended_action": top["action"] if top else None,
        "alternatives": alternatives,
        "reason": (
            "Highest expected net value among deterministic Policy-permitted actions."
            if top
            else "No recovery action is currently permitted by deterministic Policy."
        ),
        "expected_net_value_paise": top["expected_net_value"] if top else None,
        "recovery_probability": top["recovery_probability"] if top else None,
        "confidence_label": top["confidence_label"] if top else "not_applicable",
        "policy_version": policy.policy_version,
        "model_version": str(recovery_model.report["model_version"]),
        "approval_required": True,
        "action_claim_tag": top["claim_tag"] if top else None,
        "allowed_actions": list(policy.allowed_actions),
        "blocked_actions": [
            {
                "action": action,
                "reasons": reasons,
                "status": "removed_before_ai_ranking",
            }
            for action, reasons in policy.blocked_reasons.items()
        ],
    }


def _recommendation_audit(
    session: Session, incident: PaymentIncident
) -> IncidentAuditEvent | None:
    reference = incident.recommendation_reference or ""
    if not reference.startswith("incident_audit:"):
        return None
    try:
        audit_id = int(reference.split(":", 1)[1])
    except ValueError:
        return None
    audit = session.get(IncidentAuditEvent, audit_id)
    if (
        audit is None
        or audit.incident_id != incident.incident_id
        or audit.event_type != RECOMMENDATION_EVENT
    ):
        return None
    return audit


def read_incident_recommendation(session: Session, incident: PaymentIncident) -> dict | None:
    audit = _recommendation_audit(session, incident)
    if audit is None:
        return None
    recommendation = audit.payload.get("recommendation")
    return dict(recommendation) if isinstance(recommendation, dict) else None


def _existing_recommendation_for_key(
    session: Session, incident_id: str, idempotency_key: str
) -> IncidentAuditEvent | None:
    events = session.scalars(
        select(IncidentAuditEvent).where(
            IncidentAuditEvent.incident_id == incident_id,
            IncidentAuditEvent.event_type == RECOMMENDATION_EVENT,
        )
    ).all()
    return next(
        (event for event in events if event.payload.get("idempotency_key") == idempotency_key),
        None,
    )


def create_incident_recommendation(
    session: Session,
    incident: PaymentIncident,
    idempotency_key: str,
    now: datetime,
    app_state: Any,
    recovery_model: RecoveryModel,
    payment_link_test_mode: bool,
) -> tuple[dict[str, Any], bool]:
    case_ids = session.scalars(
        select(IncidentRecoveryCase.case_id)
        .where(IncidentRecoveryCase.incident_id == incident.incident_id)
        .order_by(IncidentRecoveryCase.case_id)
    ).all()
    case_recommendations: list[dict[str, Any]] = []
    for case_id in case_ids:
        case = session.get(RecoveryCase, case_id)
        if case is None:
            continue
        case_recommendations.append(
            build_case_recommendation(
                session,
                case,
                now,
                app_state,
                recovery_model,
                payment_link_test_mode,
            )
        )
    recommendation_context_hash = _canonical_hash(
        {
            "incident_id": incident.incident_id,
            "case_contexts": [
                {"case_id": item["case_id"], "context_hash": item["context_hash"]}
                for item in case_recommendations
            ],
        }
    )
    existing = _existing_recommendation_for_key(session, incident.incident_id, idempotency_key)
    if existing is not None:
        if existing.payload.get("recommendation_context_hash") != recommendation_context_hash:
            raise ValueError("idempotency key was already used for another recommendation context")
        recommendation = existing.payload.get("recommendation")
        if isinstance(recommendation, dict):
            incident.recommendation_reference = f"incident_audit:{existing.audit_id}"
            return dict(recommendation), True

    actionable = [
        item for item in case_recommendations if item["recommended_action"] is not None
    ]
    top_case = max(
        actionable,
        key=lambda item: int(item["expected_net_value_paise"] or 0),
        default=None,
    )
    recommendation = {
        "recommendation_id": f"incident_recommendation_{uuid4().hex}",
        "incident_id": incident.incident_id,
        "recommendation_version": RECOMMENDATION_VERSION,
        "recommended_case_id": top_case["case_id"] if top_case else None,
        "recommended_action": top_case["recommended_action"] if top_case else None,
        "context_hash": top_case["context_hash"] if top_case else None,
        "policy_version": top_case["policy_version"] if top_case else None,
        "case_recommendations": case_recommendations,
        "approval_required": True,
        "ranking_authority": "deterministic_recovery_model_after_policy",
        "ai_authority": "investigation_and_explanation_only",
        "estimated_amount_at_risk_paise": incident.estimated_amount_at_risk,
        "actual_recovered_amount_paise": 0,
    }
    audit = IncidentAuditEvent(
        incident_id=incident.incident_id,
        event_type=RECOMMENDATION_EVENT,
        payload={
            "idempotency_key": idempotency_key,
            "recommendation_context_hash": recommendation_context_hash,
            "recommendation": recommendation,
        },
    )
    session.add(audit)
    session.flush()
    incident.recommendation_reference = f"incident_audit:{audit.audit_id}"
    return recommendation, False


def _current_case_recommendation(
    recommendation: dict[str, Any], case_id: str
) -> dict[str, Any] | None:
    rows = recommendation.get("case_recommendations")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("case_id") == case_id:
            return row
    return None


def _assert_current_context(
    session: Session,
    recommendation: dict[str, Any],
    now: datetime,
    app_state: Any,
) -> tuple[RecoveryCase, dict[str, Any], Any]:
    case_id = recommendation.get("recommended_case_id")
    action = recommendation.get("recommended_action")
    expected_hash = recommendation.get("context_hash")
    if not isinstance(case_id, str) or not isinstance(action, str) or not isinstance(expected_hash, str):
        raise PermissionError(["no_actionable_recommendation"])
    case = session.get(RecoveryCase, case_id)
    if case is None:
        raise ValueError("recommended recovery case no longer exists")
    context, policy = _case_policy_context(session, case, now, app_state)
    current_hash = _canonical_hash(context)
    if current_hash != expected_hash:
        raise PermissionError(["stale_recommendation_context"])
    if action not in policy.allowed_actions:
        raise PermissionError(["recommended_action_no_longer_allowed"])
    return case, context, policy


def _approval_payload(decision: Decision) -> dict[str, Any] | None:
    approval = decision.reason_json.get("approval")
    return dict(approval) if isinstance(approval, dict) else None


def approve_current_recommendation(
    session: Session,
    incident: PaymentIncident,
    actor_id: str,
    now: datetime,
    app_state: Any,
) -> tuple[dict[str, Any], bool]:
    recommendation = read_incident_recommendation(session, incident)
    if recommendation is None:
        raise PermissionError(["recommendation_required"])
    case, context, policy = _assert_current_context(
        session, recommendation, now, app_state
    )
    recommendation_id = str(recommendation["recommendation_id"])
    action = str(recommendation["recommended_action"])
    execution_key = _execution_key(recommendation_id)
    decision_id = _decision_id(execution_key)
    existing = session.get(Decision, decision_id)
    if existing is not None:
        approval = _approval_payload(existing)
        if (
            existing.case_id != case.case_id
            or existing.selected_action != action
            or approval is None
            or approval.get("recommendation_id") != recommendation_id
            or approval.get("context_hash") != recommendation["context_hash"]
        ):
            raise ValueError("recommendation execution key belongs to another decision")
        if approval.get("granted") is True:
            return {
                "decision_id": existing.decision_id,
                "recommendation_id": recommendation_id,
                "case_id": case.case_id,
                "action": action,
                "policy_version": existing.policy_version,
                "context_hash": recommendation["context_hash"],
                "approved": True,
                "actor_id": approval.get("actor_id"),
            }, True

    case_row = _current_case_recommendation(recommendation, case.case_id) or {}
    approval = {
        "required": True,
        "granted": True,
        "actor_id": actor_id,
        "approved_at": now.isoformat(),
        "recommendation_id": recommendation_id,
        "incident_id": incident.incident_id,
        "case_id": case.case_id,
        "action": action,
        "policy_version": policy.policy_version,
        "context_hash": recommendation["context_hash"],
    }
    reason_json = {
        "evidence": {
            "incident_id": incident.incident_id,
            "recommendation_id": recommendation_id,
            "context_hash": recommendation["context_hash"],
            "deterministic_context": context,
        },
        "selection_source": "sentinel_recommendation",
        "rejection": None,
        "approval": approval,
    }
    if existing is None:
        existing = Decision(
            decision_id=decision_id,
            case_id=case.case_id,
            policy_version=policy.policy_version,
            model_version=str(case_row.get("model_version") or "unknown"),
            allowed_actions=list(policy.allowed_actions),
            selected_action=action,
            expected_value=int(case_row.get("expected_net_value_paise") or 0),
            reason_json=reason_json,
        )
        session.add(existing)
    else:
        existing.policy_version = policy.policy_version
        existing.allowed_actions = list(policy.allowed_actions)
        existing.selected_action = action
        existing.reason_json = reason_json
    session.flush()

    prior = session.scalars(
        select(IncidentAuditEvent).where(
            IncidentAuditEvent.incident_id == incident.incident_id,
            IncidentAuditEvent.event_type == APPROVAL_EVENT,
        )
    ).all()
    if not any(event.payload.get("recommendation_id") == recommendation_id for event in prior):
        session.add(
            IncidentAuditEvent(
                incident_id=incident.incident_id,
                event_type=APPROVAL_EVENT,
                payload={
                    "recommendation_id": recommendation_id,
                    "decision_id": decision_id,
                    "case_id": case.case_id,
                    "action": action,
                    "actor_id": actor_id,
                    "policy_version": policy.policy_version,
                    "context_hash": recommendation["context_hash"],
                    "approved_at": now.isoformat(),
                },
            )
        )
    return {
        "decision_id": decision_id,
        "recommendation_id": recommendation_id,
        "case_id": case.case_id,
        "action": action,
        "policy_version": policy.policy_version,
        "context_hash": recommendation["context_hash"],
        "approved": True,
        "actor_id": actor_id,
    }, False


def _existing_action_for_recommendation(
    session: Session, recommendation_id: str
) -> ActionEvent | None:
    return session.scalar(
        select(ActionEvent).where(ActionEvent.idempotency_key == _execution_key(recommendation_id))
    )


def execute_current_recommendation(
    session: Session,
    incident: PaymentIncident,
    now: datetime,
    app_state: Any,
) -> tuple[ActionResponse, bool]:
    recommendation = read_incident_recommendation(session, incident)
    if recommendation is None:
        raise PermissionError(["recommendation_required"])
    recommendation_id = str(recommendation.get("recommendation_id") or "")
    case_id = str(recommendation.get("recommended_case_id") or "")
    action = str(recommendation.get("recommended_action") or "")
    if not recommendation_id or not case_id or not action:
        raise PermissionError(["no_actionable_recommendation"])

    prior_action = _existing_action_for_recommendation(session, recommendation_id)
    if prior_action is not None:
        if prior_action.case_id != case_id or prior_action.tool != action:
            raise ValueError("recommendation execution key belongs to another action")
        return (
            ActionResponse(
                action=prior_action.tool,
                provider_reference=prior_action.provider_reference,
                status=prior_action.status,
            ),
            True,
        )

    execution_key = _execution_key(recommendation_id)
    decision = session.get(Decision, _decision_id(execution_key))
    approval = _approval_payload(decision) if decision is not None else None
    if decision is None or approval is None or approval.get("granted") is not True:
        raise PermissionError(["approval_required"])
    if (
        decision.case_id != case_id
        or decision.selected_action != action
        or approval.get("recommendation_id") != recommendation_id
        or approval.get("context_hash") != recommendation.get("context_hash")
        or approval.get("policy_version") != recommendation.get("policy_version")
    ):
        raise PermissionError(["approval_binding_mismatch"])

    try:
        case, _context, policy = _assert_current_context(
            session, recommendation, now, app_state
        )
    except PermissionError as error:
        session.add(
            IncidentAuditEvent(
                incident_id=incident.incident_id,
                event_type=STALE_EXECUTION_EVENT,
                payload={
                    "recommendation_id": recommendation_id,
                    "case_id": case_id,
                    "action": action,
                    "reasons": list(error.args[0]),
                },
            )
        )
        session.commit()
        raise
    if decision.policy_version != policy.policy_version:
        raise PermissionError(["stale_policy_version"])

    configuration = get_policy_configuration(session, app_state)
    try:
        result, duplicate = execute_action(
            session,
            case,
            action,
            execution_key,
            now,
            configuration.quiet_hours_start,
            configuration.quiet_hours_end,
            app_state.create_payment_link,
            configuration.kill_switch,
            configuration.contact_limit,
            configuration.policy_version,
        )
    except ProviderError:
        session.add(
            IncidentAuditEvent(
                incident_id=incident.incident_id,
                event_type=EXECUTION_FAILED_EVENT,
                payload={
                    "recommendation_id": recommendation_id,
                    "case_id": case_id,
                    "action": action,
                    "actual_recovered_amount_paise": 0,
                },
            )
        )
        session.commit()
        raise

    if incident.state == IncidentState.ACTIONABLE:
        transition_incident(
            session,
            incident,
            IncidentState.RECOVERY_IN_PROGRESS,
            payload_extra={
                "recommendation_id": recommendation_id,
                "case_id": case_id,
                "action": action,
            },
        )
    if incident.state == IncidentState.RECOVERY_IN_PROGRESS:
        transition_incident(
            session,
            incident,
            IncidentState.MONITORING,
            payload_extra={
                "recommendation_id": recommendation_id,
                "case_id": case_id,
                "awaiting_provider_evidence": True,
            },
        )
    session.add(
        IncidentAuditEvent(
            incident_id=incident.incident_id,
            event_type=EXECUTION_EVENT,
            payload={
                "recommendation_id": recommendation_id,
                "case_id": case_id,
                "action": action,
                "status": result.status,
                "provider_reference": result.provider_reference,
                "actual_recovered_amount_paise": 0,
                "awaiting_provider_evidence": True,
            },
        )
    )
    session.commit()
    return result, duplicate


def _provider_backed_outcome(session: Session, case_id: str | None) -> Outcome | None:
    if not case_id:
        return None
    outcome = session.scalar(select(Outcome).where(Outcome.case_id == case_id))
    if (
        outcome is None
        or not outcome.recovered
        or outcome.source != EvidenceSource.RAZORPAY_TEST.value
    ):
        return None
    return outcome


def incident_control_read_model(session: Session, incident: PaymentIncident) -> dict[str, Any]:
    recommendation = read_incident_recommendation(session, incident)
    analysis = read_incident_analysis(session, incident)
    recommendation_id = (
        str(recommendation.get("recommendation_id")) if recommendation else None
    )
    case_id = str(recommendation.get("recommended_case_id")) if recommendation else None
    action = str(recommendation.get("recommended_action")) if recommendation else None
    execution_key = _execution_key(recommendation_id) if recommendation_id else None
    decision = session.get(Decision, _decision_id(execution_key)) if execution_key else None
    approval = _approval_payload(decision) if decision is not None else None
    action_event = (
        session.scalar(select(ActionEvent).where(ActionEvent.idempotency_key == execution_key))
        if execution_key
        else None
    )
    outcome = _provider_backed_outcome(session, case_id)

    if outcome is not None:
        control_state = "recovered"
    elif action_event is not None and action_event.status == "failed":
        control_state = "execution_failed"
    elif action_event is not None:
        control_state = "awaiting_outcome"
    elif approval is not None and approval.get("granted") is True:
        control_state = "approved"
    elif recommendation and recommendation.get("recommended_action") is not None:
        control_state = "needs_approval"
    else:
        control_state = "no_action_available"

    return {
        "incident_id": incident.incident_id,
        "incident_state": incident.state,
        "control_state": control_state,
        "analysis": analysis,
        "recommendation": recommendation,
        "approval": approval,
        "execution": (
            {
                "action_id": action_event.action_id,
                "case_id": action_event.case_id,
                "action": action_event.tool,
                "status": action_event.status,
                "provider_reference": action_event.provider_reference,
            }
            if action_event is not None
            else None
        ),
        "estimated_amount_at_risk_paise": incident.estimated_amount_at_risk,
        "actual_recovered_amount_paise": outcome.recovered_amount if outcome else 0,
        "actual_recovered_claim_tag": ClaimTag.TEST_MODE.value if outcome else None,
        "recommended_case_id": case_id,
        "recommended_action": action,
        "awaiting_provider_evidence": control_state == "awaiting_outcome",
    }
