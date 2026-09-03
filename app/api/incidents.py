from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.db.tables import IncidentAuditEvent, IncidentPaymentEvent, PaymentIncident
from app.domain.incidents import (
    build_incident_evidence_bundle,
    correlate_payment,
    incident_case_chain,
    link_case_to_incident,
    link_event_to_incident,
)

router = APIRouter(prefix="/api/v1", tags=["incidents"])


class IncidentLinkRequest(BaseModel):
    event_id: str | None = None
    case_id: str | None = None


def _incident_summary(incident: PaymentIncident) -> dict[str, Any]:
    return {
        "incident_id": incident.incident_id,
        "state": incident.state,
        "opened_at": incident.opened_at.isoformat(),
        "updated_at": incident.updated_at.isoformat(),
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        "detection_version": incident.detection_version,
        "cohort_filter": incident.cohort_filter,
        "baseline_metrics": incident.baseline_metrics,
        "observed_metrics": incident.observed_metrics,
        "affected_attempt_count": incident.affected_attempt_count,
        "estimated_amount_at_risk": incident.estimated_amount_at_risk,
        "confidence": incident.confidence,
        "provenance_summary": incident.provenance_summary_json,
        "analysis_reference": incident.analysis_reference,
        "recommendation_reference": incident.recommendation_reference,
    }


@router.get("/incidents")
def list_incidents(request: Request) -> list[dict[str, Any]]:
    with request.app.state.session_factory() as session:
        incidents = session.scalars(
            select(PaymentIncident).order_by(PaymentIncident.opened_at.desc())
        ).all()
        return [_incident_summary(incident) for incident in incidents]


@router.get("/correlation/payment")
def get_payment_correlation(
    request: Request,
    provider_payment_id: str | None = Query(default=None),
    provider_order_id: str | None = Query(default=None),
    merchant_order_reference: str | None = Query(default=None),
) -> dict[str, list[str]]:
    with request.app.state.session_factory() as session:
        try:
            return correlate_payment(
                session,
                provider_payment_id=provider_payment_id,
                provider_order_id=provider_order_id,
                merchant_order_reference=merchant_order_reference,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/incidents/{incident_id}/links")
def link_incident_entities(
    incident_id: str, link: IncidentLinkRequest, request: Request
) -> dict[str, Any]:
    if link.event_id is None and link.case_id is None:
        raise HTTPException(status_code=422, detail="event_id or case_id is required")
    with request.app.state.session_factory() as session:
        if session.get(PaymentIncident, incident_id) is None:
            raise HTTPException(status_code=404, detail="incident not found")
        try:
            event_linked = (
                link_event_to_incident(session, incident_id, link.event_id)
                if link.event_id
                else False
            )
            case_linked = (
                link_case_to_incident(session, incident_id, link.case_id)
                if link.case_id
                else False
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        session.commit()
        return {
            "incident_id": incident_id,
            "event_linked": event_linked,
            "case_linked": case_linked,
        }


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: str, request: Request) -> dict[str, Any]:
    with request.app.state.session_factory() as session:
        incident = session.get(PaymentIncident, incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="incident not found")
        event_ids = session.scalars(
            select(IncidentPaymentEvent.event_id).where(
                IncidentPaymentEvent.incident_id == incident_id
            )
        ).all()
        audit = session.scalars(
            select(IncidentAuditEvent)
            .where(IncidentAuditEvent.incident_id == incident_id)
            .order_by(IncidentAuditEvent.audit_id)
        ).all()
        detail = _incident_summary(incident)
        detail.update(
            {
                "detection_evidence": incident.detection_evidence_json,
                "linked_event_ids": list(event_ids),
                "case_chain": incident_case_chain(session, incident_id),
                "evidence_bundle": build_incident_evidence_bundle(
                    session, incident
                ).model_dump(mode="json"),
                "audit": [
                    {
                        "event_type": item.event_type,
                        "payload": item.payload,
                        "created_at": item.created_at.isoformat(),
                    }
                    for item in audit
                ],
            }
        )
        return detail
