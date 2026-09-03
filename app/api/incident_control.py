from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.tables import IncidentAuditEvent, PaymentIncident, RecoveryCase
from app.domain.enums import IncidentState
from app.domain.incidents import build_incident_evidence_bundle, incident_case_chain, transition_incident
from app.domain.models import DecisionRequest
from app.incident_analysis import create_incident_analysis, read_incident_analysis
from app.incident_recovery import (
    create_incident_recommendation,
    mark_merchant_notified,
    merchant_notification_state,
    read_incident_recommendation,
    run_incident_recovery,
)
from app.policy import get_policy_configuration
from app.recovery.actions import ProviderError

router = APIRouter(prefix="/api/v1", tags=["incident-control"])


class IncidentInvestigateRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


def _incident_or_404(session, incident_id: str) -> PaymentIncident:
    incident = session.get(PaymentIncident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return incident


def _control_payload(session, incident: PaymentIncident) -> dict:
    return {
        "incident_id": incident.incident_id,
        "incident_state": incident.state,
        "evidence_bundle": build_incident_evidence_bundle(session, incident).model_dump(mode="json"),
        "analysis": read_incident_analysis(session, incident),
        "recommendation": read_incident_recommendation(session, incident),
        "merchant_notification": merchant_notification_state(session, incident),
        "case_chain": incident_case_chain(session, incident.incident_id),
    }


@router.post("/incidents/{incident_id}/investigate")
def investigate_incident(
    incident_id: str, payload: IncidentInvestigateRequest, request: Request
) -> dict:
    with request.app.state.session_factory() as session:
        incident = _incident_or_404(session, incident_id)
        if incident.state == IncidentState.DETECTED:
            transition_incident(
                session,
                incident,
                IncidentState.INVESTIGATING,
                payload_extra={"trigger": "background_investigation"},
            )
        elif incident.state not in {IncidentState.INVESTIGATING, IncidentState.ACTIONABLE}:
            raise HTTPException(status_code=409, detail="incident is not investigatable")

        try:
            analysis, _ = create_incident_analysis(
                session,
                incident,
                f"{payload.idempotency_key}:analysis",
                request.app.state.incident_analysis_provider,
            )
            configuration = get_policy_configuration(session, request.app.state)
            recommendation, _ = create_incident_recommendation(
                session,
                incident,
                f"{payload.idempotency_key}:recommendation",
                request.app.state.policy_now(),
                configuration.quiet_hours_start,
                configuration.quiet_hours_end,
                configuration.kill_switch,
                configuration.contact_limit,
                request.app.state.recovery_model,
                request.app.state.payment_link_test_mode,
                configuration.policy_version,
            )
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error

        if incident.state == IncidentState.INVESTIGATING:
            transition_incident(
                session,
                incident,
                IncidentState.ACTIONABLE,
                payload_extra={
                    "analysis_reference": incident.analysis_reference,
                    "recommendation_reference": incident.recommendation_reference,
                },
            )
        review_event = session.scalar(
            select(IncidentAuditEvent.audit_id).where(
                IncidentAuditEvent.incident_id == incident.incident_id,
                IncidentAuditEvent.event_type == "merchant.review_required",
            )
        )
        if review_event is None:
            session.add(
                IncidentAuditEvent(
                    incident_id=incident.incident_id,
                    event_type="merchant.review_required",
                    payload={
                        "channel": "in_product",
                        "analysis_reference": incident.analysis_reference,
                        "recommendation_reference": incident.recommendation_reference,
                    },
                )
            )
        session.commit()
        result = _control_payload(session, incident)
        result["analysis"] = analysis
        result["recommendation"] = recommendation
        return result


@router.get("/incidents/{incident_id}/control")
def get_incident_control(incident_id: str, request: Request) -> dict:
    with request.app.state.session_factory() as session:
        incident = _incident_or_404(session, incident_id)
        return _control_payload(session, incident)


@router.post("/incidents/{incident_id}/notify")
def notify_merchant(incident_id: str, request: Request) -> dict:
    with request.app.state.session_factory() as session:
        incident = _incident_or_404(session, incident_id)
        try:
            notification = mark_merchant_notified(session, incident)
        except PermissionError as error:
            raise HTTPException(status_code=409, detail=error.args[0]) from error
        session.commit()
        return notification


@router.post("/incidents/{incident_id}/cases/{case_id}/decisions", status_code=201)
def incident_decision(
    incident_id: str,
    case_id: str,
    decision_request: DecisionRequest,
    request: Request,
) -> dict:
    if (
        decision_request.approved
        and request.headers.get("X-Reroute-Role", "operations_worker") != "business_owner"
    ):
        raise HTTPException(status_code=403, detail="business owner role required")
    with request.app.state.session_factory() as session:
        incident = _incident_or_404(session, incident_id)
        case = session.get(RecoveryCase, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        configuration = get_policy_configuration(session, request.app.state)
        try:
            result, duplicate = run_incident_recovery(
                session,
                incident,
                case,
                decision_request.idempotency_key,
                request.app.state.policy_now(),
                configuration.quiet_hours_start,
                configuration.quiet_hours_end,
                request.app.state.create_payment_link,
                request.app.state.recovery_model,
                request.app.state.decide_recovery_action,
                decision_request.approved,
                decision_request.selected_action,
                configuration.kill_switch,
                configuration.contact_limit,
                configuration.policy_version,
            )
        except ProviderError as error:
            raise HTTPException(status_code=502, detail=error.public_message) from error
        except PermissionError as error:
            raise HTTPException(status_code=409, detail=error.args[0]) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        response = result.model_dump(mode="json")
        response["duplicate"] = duplicate
        response["incident_id"] = incident.incident_id
        response["merchant_notification"] = merchant_notification_state(session, incident)
        return response
