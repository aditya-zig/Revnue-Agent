"""Deterministic incident recommendation, approval and recovery orchestration."""

from collections.abc import Callable
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
from app.domain.enums import ClaimTag, IncidentState
from app.domain.incidents import transition_incident
from app.domain.models import DecisionResponse
from app.recovery.actions import ProviderError
from app.recovery.controller import evaluate_decision_policy, run_decision
from app.recovery.scoring import RecoveryModel

RECOMMENDATION_VERSION = "sentinel-recommendation-v1"


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


def _blocked_before_ai(policy) -> list[dict[str, Any]]:
    return [
        {
            "action": action,
            "reasons": reasons,
            "status": "removed_before_ai_ranking",
        }
        for action, reasons in policy.blocked_reasons.items()
    ]


def build_case_recommendation(
    session: Session,
    case: RecoveryCase,
    now: datetime,
    quiet_hours_start: int,
    quiet_hours_end: int,
    kill_switch: bool,
    contact_limit: int,
    recovery_model: RecoveryModel,
    payment_link_test_mode: bool,
    policy_version: str = "v1",
) -> dict[str, Any]:
    """Rank only deterministic Policy-permitted actions for one RecoveryCase."""
    policy = evaluate_decision_policy(
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
        "recommendation_version": RECOMMENDATION_VERSION,
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
        "allowed_actions": policy.allowed_actions,
        "blocked_actions": _blocked_before_ai(policy),
    }


def _existing_recommendation(
    session: Session, incident_id: str, idempotency_key: str
) -> IncidentAuditEvent | None:
    events = session.scalars(
        select(IncidentAuditEvent).where(
            IncidentAuditEvent.incident_id == incident_id,
            IncidentAuditEvent.event_type == "incident.recommendation.ready",
        )
    ).all()
    return next(
        (
            event
            for event in events
            if event.payload.get("idempotency_key") == idempotency_key
        ),
        None,
    )


def create_incident_recommendation(
    session: Session,
    incident: PaymentIncident,
    idempotency_key: str,
    now: datetime,
    quiet_hours_start: int,
    quiet_hours_end: int,
    kill_switch: bool,
    contact_limit: int,
    recovery_model: RecoveryModel,
    payment_link_test_mode: bool,
    policy_version: str = "v1",
) -> tuple[dict[str, Any], bool]:
    existing = _existing_recommendation(session, incident.incident_id, idempotency_key)
    if existing is not None:
        recommendation = existing.payload.get("recommendation")
        if isinstance(recommendation, dict):
            return dict(recommendation), True

    case_ids = session.scalars(
        select(IncidentRecoveryCase.case_id).where(
            IncidentRecoveryCase.incident_id == incident.incident_id
        )
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
                quiet_hours_start,
                quiet_hours_end,
                kill_switch,
                contact_limit,
                recovery_model,
                payment_link_test_mode,
                policy_version,
            )
        )
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
        "case_recommendations": case_recommendations,
        "approval_required": True,
        "ranking_authority": "deterministic_recovery_model_after_policy",
        "ai_authority": "explanation_only",
    }
    audit = IncidentAuditEvent(
        incident_id=incident.incident_id,
        event_type="incident.recommendation.ready",
        payload={
            "idempotency_key": idempotency_key,
            "recommendation": recommendation,
        },
    )
    session.add(audit)
    session.flush()
    incident.recommendation_reference = f"incident_audit:{audit.audit_id}"
    return recommendation, False


def read_incident_recommendation(session: Session, incident: PaymentIncident) -> dict | None:
    reference = incident.recommendation_reference or ""
    if not reference.startswith("incident_audit:"):
        return None
    try:
        audit_id = int(reference.split(":", 1)[1])
    except ValueError:
        return None
    audit = session.get(IncidentAuditEvent, audit_id)
    if audit is None or audit.incident_id != incident.incident_id:
        return None
    recommendation = audit.payload.get("recommendation")
    return dict(recommendation) if isinstance(recommendation, dict) else None


def merchant_notification_state(session: Session, incident: PaymentIncident) -> dict[str, Any]:
    notified = session.scalar(
        select(IncidentAuditEvent.audit_id).where(
            IncidentAuditEvent.incident_id == incident.incident_id,
            IncidentAuditEvent.event_type == "merchant.notified",
        )
    )
    if incident.state in {IncidentState.DETECTED, IncidentState.INVESTIGATING}:
        state = "investigating"
    elif incident.state == IncidentState.ACTIONABLE:
        state = "merchant_notified" if notified else "needs_review"
    elif incident.state == IncidentState.RECOVERY_IN_PROGRESS:
        state = "approved_executing"
    elif incident.state == IncidentState.MONITORING:
        state = "monitoring_outcome"
    else:
        state = "resolved"
    return {
        "state": state,
        "merchant_notified": bool(notified),
        "needs_review": state == "needs_review",
        "analysis_reference": incident.analysis_reference,
        "recommendation_reference": incident.recommendation_reference,
    }


def mark_merchant_notified(session: Session, incident: PaymentIncident) -> dict[str, Any]:
    if incident.state != IncidentState.ACTIONABLE or not incident.recommendation_reference:
        raise PermissionError(["incident_not_actionable"])
    existing = session.scalar(
        select(IncidentAuditEvent).where(
            IncidentAuditEvent.incident_id == incident.incident_id,
            IncidentAuditEvent.event_type == "merchant.notified",
        )
    )
    if existing is None:
        session.add(
            IncidentAuditEvent(
                incident_id=incident.incident_id,
                event_type="merchant.notified",
                payload={
                    "channel": "in_product",
                    "needs_review": True,
                    "recommendation_reference": incident.recommendation_reference,
                },
            )
        )
        session.flush()
    return merchant_notification_state(session, incident)


def _incident_event_for_key(
    session: Session, incident_id: str, event_type: str, idempotency_key: str
) -> IncidentAuditEvent | None:
    events = session.scalars(
        select(IncidentAuditEvent).where(
            IncidentAuditEvent.incident_id == incident_id,
            IncidentAuditEvent.event_type == event_type,
        )
    ).all()
    return next(
        (event for event in events if event.payload.get("idempotency_key") == idempotency_key),
        None,
    )


def run_incident_recovery(
    session: Session,
    incident: PaymentIncident,
    case: RecoveryCase,
    idempotency_key: str,
    now: datetime,
    quiet_hours_start: int,
    quiet_hours_end: int,
    create_payment_link: Callable[[int, str], str],
    recovery_model: RecoveryModel,
    decide_recovery_action: Callable[[dict], object] | None,
    approved: bool,
    requested_action: str | None,
    kill_switch: bool,
    contact_limit: int,
    policy_version: str = "v1",
) -> tuple[DecisionResponse, bool]:
    link = session.get(
        IncidentRecoveryCase,
        {"incident_id": incident.incident_id, "case_id": case.case_id},
    )
    if link is None:
        raise ValueError("recovery case is not linked to incident")
    try:
        decision, duplicate = run_decision(
            session,
            case,
            idempotency_key,
            now,
            quiet_hours_start,
            quiet_hours_end,
            create_payment_link,
            recovery_model,
            decide_recovery_action,
            approved=approved,
            requested_action=requested_action,
            kill_switch=kill_switch,
            contact_limit=contact_limit,
            policy_version=policy_version,
        )
    except ProviderError:
        if _incident_event_for_key(
            session,
            incident.incident_id,
            "incident.recovery.provider_failure",
            idempotency_key,
        ) is None:
            action = session.scalar(
                select(ActionEvent).where(ActionEvent.idempotency_key == idempotency_key)
            )
            session.add(
                IncidentAuditEvent(
                    incident_id=incident.incident_id,
                    event_type="incident.recovery.provider_failure",
                    payload={
                        "case_id": case.case_id,
                        "idempotency_key": idempotency_key,
                        "action_id": action.action_id if action else None,
                        "outcome_recorded": False,
                    },
                )
            )
            session.commit()
        raise

    event_type = (
        "incident.recovery.execution_started"
        if approved and decision.action is not None
        else "incident.decision.awaiting_approval"
    )
    if _incident_event_for_key(session, incident.incident_id, event_type, idempotency_key) is None:
        action = session.scalar(
            select(ActionEvent).where(ActionEvent.idempotency_key == idempotency_key)
        )
        session.add(
            IncidentAuditEvent(
                incident_id=incident.incident_id,
                event_type=event_type,
                payload={
                    "case_id": case.case_id,
                    "decision_id": decision.decision_id,
                    "selected_action": decision.selected_action,
                    "policy_version": decision.policy_version,
                    "model_version": decision.model_version,
                    "approval_granted": approved,
                    "action_id": action.action_id if action else None,
                    "idempotency_key": idempotency_key,
                },
            )
        )
    if approved and decision.action is not None and incident.state == IncidentState.ACTIONABLE:
        transition_incident(
            session,
            incident,
            IncidentState.RECOVERY_IN_PROGRESS,
            payload_extra={"case_id": case.case_id, "decision_id": decision.decision_id},
        )
    session.commit()
    return decision, duplicate


def link_provider_outcome_to_incidents(
    session: Session,
    case: RecoveryCase,
    outcome: Outcome,
    *,
    event_id: str,
    provider_event_id: str,
    payment_id: str,
    amount: int,
    source: str,
) -> None:
    """Write the authoritative provider-evidence link after Outcome exists."""
    links = session.scalars(
        select(IncidentRecoveryCase).where(IncidentRecoveryCase.case_id == case.case_id)
    ).all()
    if not links:
        return
    decision = session.scalar(
        select(Decision)
        .where(Decision.case_id == case.case_id)
        .order_by(Decision.created_at.desc())
    )
    action = session.scalar(
        select(ActionEvent)
        .where(ActionEvent.case_id == case.case_id)
        .order_by(ActionEvent.executed_at.desc())
    )
    for link in links:
        incident = session.get(PaymentIncident, link.incident_id)
        if incident is None:
            continue
        existing = session.scalar(
            select(IncidentAuditEvent.audit_id).where(
                IncidentAuditEvent.incident_id == incident.incident_id,
                IncidentAuditEvent.event_type == "incident.outcome.provider_verified",
            )
        )
        if existing is None:
            session.add(
                IncidentAuditEvent(
                    incident_id=incident.incident_id,
                    event_type="incident.outcome.provider_verified",
                    payload={
                        "case_id": case.case_id,
                        "decision_id": decision.decision_id if decision else None,
                        "action_id": action.action_id if action else None,
                        "outcome_id": outcome.outcome_id,
                        "event_id": event_id,
                        "provider_event_id": provider_event_id,
                        "payment_id": payment_id,
                        "amount": amount,
                        "source": source,
                        "claim_tag": ClaimTag.TEST_MODE.value,
                    },
                )
            )
        if incident.state in {IncidentState.ACTIONABLE, IncidentState.RECOVERY_IN_PROGRESS}:
            transition_incident(
                session,
                incident,
                IncidentState.MONITORING,
                payload_extra={
                    "case_id": case.case_id,
                    "outcome_id": outcome.outcome_id,
                    "provider_event_id": provider_event_id,
                },
            )
