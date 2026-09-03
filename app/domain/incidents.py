from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.tables import (
    ActionEvent,
    Decision,
    IncidentAuditEvent,
    IncidentPaymentEvent,
    IncidentRecoveryCase,
    Outcome,
    PaymentEvent,
    PaymentIncident,
    RecoveryCase,
)
from app.domain.enums import EvidenceSource, IncidentState


class IncidentEvidenceReference(BaseModel):
    event_id: str
    provider: str
    source_kind: EvidenceSource
    provider_event_id: str
    provider_payment_id: str
    provider_order_id: str | None = None
    merchant_order_reference: str | None = None
    occurred_at: datetime
    amount: int
    currency: str
    method: str | None = None
    status: str
    error_source: str | None = None
    error_step: str | None = None
    error_code: str | None = None
    error_reason: str | None = None
    evidence_hash: str
    authenticity_verified: bool


class IncidentEvidenceBundle(BaseModel):
    """Sanitized deterministic facts for later bounded AI analysis.

    ``model_hypotheses`` is intentionally separate and empty at this foundation
    layer. Session 3 may populate it, but hypotheses must never be written into
    the observed-facts section.
    """

    incident_id: str
    observed_facts: dict[str, Any]
    evidence: list[IncidentEvidenceReference]
    model_hypotheses: list[dict[str, Any]] = Field(default_factory=list)


INCIDENT_TRANSITIONS: dict[IncidentState, set[IncidentState]] = {
    IncidentState.DETECTED: {IncidentState.INVESTIGATING},
    IncidentState.INVESTIGATING: {IncidentState.ACTIONABLE},
    IncidentState.ACTIONABLE: {
        IncidentState.RECOVERY_IN_PROGRESS,
        IncidentState.MONITORING,
    },
    IncidentState.RECOVERY_IN_PROGRESS: {IncidentState.MONITORING},
    IncidentState.MONITORING: {IncidentState.RESOLVED},
}


def transition_incident(
    session: Session,
    incident: PaymentIncident,
    target: IncidentState,
    payload_extra: dict[str, Any] | None = None,
) -> None:
    current = IncidentState(incident.state)
    if target not in INCIDENT_TRANSITIONS.get(current, set()):
        raise ValueError(f"cannot transition a {current} incident to {target}")
    now = datetime.now(UTC)
    incident.state = target
    incident.updated_at = now
    if target == IncidentState.RESOLVED:
        incident.resolved_at = now
    payload: dict[str, Any] = {"from": current, "to": target}
    if payload_extra:
        payload.update(payload_extra)
    session.add(
        IncidentAuditEvent(
            incident_id=incident.incident_id,
            event_type=f"incident.{target}",
            payload=payload,
        )
    )


def link_event_to_incident(session: Session, incident_id: str, event_id: str) -> bool:
    if session.get(PaymentIncident, incident_id) is None:
        raise ValueError("incident not found")
    if session.get(PaymentEvent, event_id) is None:
        raise ValueError("payment event not found")
    key = {"incident_id": incident_id, "event_id": event_id}
    if session.get(IncidentPaymentEvent, key) is not None:
        return False
    session.add(IncidentPaymentEvent(**key))
    return True


def link_case_to_incident(session: Session, incident_id: str, case_id: str) -> bool:
    if session.get(PaymentIncident, incident_id) is None:
        raise ValueError("incident not found")
    if session.get(RecoveryCase, case_id) is None:
        raise ValueError("recovery case not found")
    key = {"incident_id": incident_id, "case_id": case_id}
    if session.get(IncidentRecoveryCase, key) is not None:
        return False
    session.add(IncidentRecoveryCase(**key))
    return True


def build_incident_evidence_bundle(
    session: Session, incident: PaymentIncident
) -> IncidentEvidenceBundle:
    event_ids = session.scalars(
        select(IncidentPaymentEvent.event_id).where(
            IncidentPaymentEvent.incident_id == incident.incident_id
        )
    ).all()
    events = []
    if event_ids:
        events = session.scalars(
            select(PaymentEvent)
            .where(PaymentEvent.event_id.in_(event_ids))
            .order_by(PaymentEvent.occurred_at, PaymentEvent.event_id)
        ).all()
    evidence = [
        IncidentEvidenceReference(
            event_id=event.event_id,
            provider=event.provider,
            source_kind=EvidenceSource(event.source_kind),
            provider_event_id=event.provider_event_id,
            provider_payment_id=event.payment_id,
            provider_order_id=event.provider_order_id,
            merchant_order_reference=event.merchant_order_reference,
            occurred_at=event.occurred_at,
            amount=event.amount,
            currency=event.currency,
            method=event.method,
            status=event.status,
            error_source=event.error_source,
            error_step=event.error_step,
            error_code=event.error_code,
            error_reason=event.error_reason,
            evidence_hash=event.raw_hash,
            authenticity_verified=event.authenticity_verified,
        )
        for event in events
    ]
    return IncidentEvidenceBundle(
        incident_id=incident.incident_id,
        observed_facts={
            "detection_version": incident.detection_version,
            "cohort_filter": incident.cohort_filter,
            "baseline_metrics": incident.baseline_metrics,
            "observed_metrics": incident.observed_metrics,
            "affected_attempt_count": incident.affected_attempt_count,
            "estimated_amount_at_risk": incident.estimated_amount_at_risk,
            "confidence": incident.confidence,
            "detection_evidence": incident.detection_evidence_json,
            "provenance_summary": incident.provenance_summary_json,
        },
        evidence=evidence,
    )


def incident_case_chain(session: Session, incident_id: str) -> list[dict[str, Any]]:
    case_ids = session.scalars(
        select(IncidentRecoveryCase.case_id).where(
            IncidentRecoveryCase.incident_id == incident_id
        )
    ).all()
    chain: list[dict[str, Any]] = []
    for case_id in case_ids:
        case = session.get(RecoveryCase, case_id)
        if case is None:
            continue
        decisions = session.scalars(
            select(Decision.decision_id).where(Decision.case_id == case_id)
        ).all()
        actions = session.scalars(
            select(ActionEvent.action_id).where(ActionEvent.case_id == case_id)
        ).all()
        outcome_ids = session.scalars(
            select(Outcome.outcome_id).where(Outcome.case_id == case_id)
        ).all()
        chain.append(
            {
                "case_id": case_id,
                "payment_id": case.payment_id,
                "obligation_reference": case.obligation_reference,
                "decision_ids": list(decisions),
                "action_ids": list(actions),
                "outcome_ids": list(outcome_ids),
            }
        )
    return chain


def correlate_payment(
    session: Session,
    *,
    provider_payment_id: str | None = None,
    provider_order_id: str | None = None,
    merchant_order_reference: str | None = None,
) -> dict[str, list[str]]:
    conditions = []
    if provider_payment_id:
        conditions.append(PaymentEvent.payment_id == provider_payment_id)
    if provider_order_id:
        conditions.extend(
            [
                PaymentEvent.provider_order_id == provider_order_id,
                PaymentEvent.obligation_reference == provider_order_id,
            ]
        )
    if merchant_order_reference:
        conditions.extend(
            [
                PaymentEvent.merchant_order_reference == merchant_order_reference,
                PaymentEvent.obligation_reference == merchant_order_reference,
            ]
        )
    if not conditions:
        raise ValueError("at least one payment correlation identifier is required")

    events = session.scalars(
        select(PaymentEvent).where(or_(*conditions)).order_by(PaymentEvent.occurred_at)
    ).all()
    event_ids = list(dict.fromkeys(event.event_id for event in events))
    payment_ids = list(dict.fromkeys(event.payment_id for event in events))
    obligation_references = list(
        dict.fromkeys(
            event.obligation_reference for event in events if event.obligation_reference
        )
    )

    case_conditions = []
    if payment_ids:
        case_conditions.append(RecoveryCase.payment_id.in_(payment_ids))
    if obligation_references:
        case_conditions.append(RecoveryCase.obligation_reference.in_(obligation_references))
    if merchant_order_reference:
        case_conditions.append(RecoveryCase.obligation_reference == merchant_order_reference)
    if provider_order_id:
        case_conditions.append(RecoveryCase.obligation_reference == provider_order_id)
    cases = []
    if case_conditions:
        cases = session.scalars(select(RecoveryCase).where(or_(*case_conditions))).all()
    case_ids = list(dict.fromkeys(case.case_id for case in cases))

    incident_ids: list[str] = []
    if event_ids:
        incident_ids.extend(
            session.scalars(
                select(IncidentPaymentEvent.incident_id).where(
                    IncidentPaymentEvent.event_id.in_(event_ids)
                )
            ).all()
        )
    if case_ids:
        incident_ids.extend(
            session.scalars(
                select(IncidentRecoveryCase.incident_id).where(
                    IncidentRecoveryCase.case_id.in_(case_ids)
                )
            ).all()
        )

    return {
        "event_ids": event_ids,
        "case_ids": case_ids,
        "incident_ids": list(dict.fromkeys(incident_ids)),
    }
