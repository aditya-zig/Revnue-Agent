"""Provider-evidence authority boundary for Sentinel incident outcomes."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import (
    IncidentAuditEvent,
    IncidentRecoveryCase,
    Outcome,
    PaymentIncident,
    RecoveryCase,
)
from app.domain.enums import EvidenceSource, IncidentState
from app.domain.incidents import transition_incident
from app.domain.models import NormalizedPaymentEvent

PROVIDER_OUTCOME_EVENT = "incident.outcome.provider_verified"


def _verified_outcome_payload(
    case: RecoveryCase,
    outcome: Outcome,
    event: NormalizedPaymentEvent,
) -> dict[str, object]:
    if event.provider != EvidenceSource.RAZORPAY_TEST.value:
        raise ValueError("provider outcome is not Razorpay Test Mode evidence")
    if event.source_kind != EvidenceSource.RAZORPAY_TEST:
        raise ValueError("provider outcome source is not authenticated Razorpay Test Mode")
    if not event.authenticity_verified:
        raise ValueError("provider outcome authenticity is not verified")
    if not outcome.recovered or outcome.source != EvidenceSource.RAZORPAY_TEST.value:
        raise ValueError("outcome is not provider-backed recovered revenue")
    if outcome.recovered_amount != event.amount or case.amount_at_risk != event.amount:
        raise ValueError("provider outcome amount does not match the recovery obligation")
    return {
        "case_id": case.case_id,
        "outcome_id": outcome.outcome_id,
        "event_id": event.event_id,
        "provider_event_id": event.provider_event_id,
        "payment_id": event.payment_id,
        "obligation_reference": event.obligation_reference,
        "amount": event.amount,
        "source": EvidenceSource.RAZORPAY_TEST.value,
        "authenticity_verified": True,
        "claim_tag": "TEST MODE",
    }


def _same_provider_evidence(existing: dict, expected: dict[str, object]) -> bool:
    keys = {
        "case_id",
        "outcome_id",
        "event_id",
        "provider_event_id",
        "payment_id",
        "obligation_reference",
        "amount",
        "source",
        "authenticity_verified",
    }
    return all(existing.get(key) == expected.get(key) for key in keys)


def link_verified_provider_outcome_to_incidents(
    session: Session,
    case: RecoveryCase,
    outcome: Outcome,
    event: NormalizedPaymentEvent,
) -> None:
    """Link recovered money only after authenticated provider evidence exists.

    An identical replay of the same provider event is idempotent. Reusing the
    same provider event ID with conflicting facts fails closed.
    """

    expected = _verified_outcome_payload(case, outcome, event)
    links = session.scalars(
        select(IncidentRecoveryCase).where(IncidentRecoveryCase.case_id == case.case_id)
    ).all()
    for link in links:
        incident = session.get(PaymentIncident, link.incident_id)
        if incident is None:
            continue
        existing_events = session.scalars(
            select(IncidentAuditEvent).where(
                IncidentAuditEvent.incident_id == incident.incident_id,
                IncidentAuditEvent.event_type == PROVIDER_OUTCOME_EVENT,
            )
        ).all()
        matched = False
        for audit in existing_events:
            if audit.payload.get("provider_event_id") != event.provider_event_id:
                continue
            if not _same_provider_evidence(audit.payload, expected):
                raise ValueError("conflicting provider outcome evidence")
            matched = True
            break
        if not matched:
            session.add(
                IncidentAuditEvent(
                    incident_id=incident.incident_id,
                    event_type=PROVIDER_OUTCOME_EVENT,
                    payload=expected,
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
                    "provider_event_id": event.provider_event_id,
                },
            )
