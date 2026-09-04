from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.db.tables import PaymentIncident
from app.domain.enums import IncidentState
from app.domain.incidents import transition_incident
from app.incident_analysis import OpenRouterIncidentProvider, create_incident_analysis
from app.incident_control import (
    approve_current_recommendation,
    create_incident_recommendation,
    execute_current_recommendation,
    incident_control_read_model,
)
from app.recovery.actions import ProviderError

router = APIRouter(prefix="/api/v1", tags=["incident-control"])


class IncidentInvestigateRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


def _owner_actor(request: Request) -> str:
    actor_id = getattr(request.app.state, "sentinel_owner_actor_id", None)
    if not isinstance(actor_id, str) or not actor_id:
        raise HTTPException(status_code=403, detail="business owner role required")
    return actor_id


def _incident_provider(request: Request):
    configured = getattr(request.app.state, "incident_analysis_provider", None)
    if configured is not None:
        return configured
    settings = request.app.state.settings
    return OpenRouterIncidentProvider(
        api_key=settings.openrouter_api_key,
        timeout=settings.openrouter_timeout_seconds,
        http_referer=settings.openrouter_http_referer or None,
    )


def _incident_or_404(session, incident_id: str) -> PaymentIncident:
    incident = session.get(PaymentIncident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return incident


def _permission_conflict(error: PermissionError) -> HTTPException:
    detail = error.args[0] if error.args else ["operation_not_permitted"]
    return HTTPException(status_code=409, detail=detail)


@router.post("/incidents/{incident_id}/investigate")
def investigate_incident(
    incident_id: str,
    payload: IncidentInvestigateRequest,
    request: Request,
) -> dict[str, Any]:
    with request.app.state.session_factory() as session:
        incident = _incident_or_404(session, incident_id)
        if (
            incident.state not in {IncidentState.DETECTED, IncidentState.INVESTIGATING}
            and incident.analysis_reference
            and incident.recommendation_reference
        ):
            return incident_control_read_model(session, incident)
        if incident.state == IncidentState.DETECTED:
            transition_incident(session, incident, IncidentState.INVESTIGATING)
        elif incident.state != IncidentState.INVESTIGATING:
            raise HTTPException(status_code=409, detail="incident cannot be investigated")

        try:
            create_incident_analysis(
                session,
                incident,
                payload.idempotency_key,
                _incident_provider(request),
            )
            create_incident_recommendation(
                session,
                incident,
                f"{payload.idempotency_key}:recommendation",
                request.app.state.policy_now(),
                request.app.state,
                request.app.state.recovery_model,
                request.app.state.razorpay_key_id.startswith("rzp_test_"),
            )
            if incident.state == IncidentState.INVESTIGATING:
                transition_incident(session, incident, IncidentState.ACTIONABLE)
            session.commit()
        except (PermissionError, ValueError) as error:
            session.rollback()
            if isinstance(error, PermissionError):
                raise _permission_conflict(error) from error
            raise HTTPException(status_code=409, detail=str(error)) from error
        return incident_control_read_model(session, incident)


@router.get("/incidents/{incident_id}/control")
def get_incident_control(incident_id: str, request: Request) -> dict[str, Any]:
    with request.app.state.session_factory() as session:
        incident = _incident_or_404(session, incident_id)
        return incident_control_read_model(session, incident)


@router.post("/incidents/{incident_id}/approve")
def approve_incident_recommendation(
    incident_id: str,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    actor_id = _owner_actor(request)
    with request.app.state.session_factory() as session:
        incident = _incident_or_404(session, incident_id)
        if incident.state != IncidentState.ACTIONABLE:
            raise HTTPException(status_code=409, detail="incident is not actionable")
        try:
            approval, duplicate = approve_current_recommendation(
                session,
                incident,
                actor_id,
                request.app.state.policy_now(),
                request.app.state,
            )
            session.commit()
        except PermissionError as error:
            session.rollback()
            raise _permission_conflict(error) from error
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        if duplicate:
            response.status_code = status.HTTP_200_OK
        else:
            response.status_code = status.HTTP_201_CREATED
        return approval


@router.post("/incidents/{incident_id}/execute")
def execute_incident_recommendation(
    incident_id: str,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    _owner_actor(request)
    with request.app.state.session_factory() as session:
        incident = _incident_or_404(session, incident_id)
        try:
            result, duplicate = execute_current_recommendation(
                session,
                incident,
                request.app.state.policy_now(),
                request.app.state,
            )
        except PermissionError as error:
            session.rollback()
            raise _permission_conflict(error) from error
        except ProviderError as error:
            session.rollback()
            raise HTTPException(status_code=502, detail=error.public_message) from error
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        if duplicate:
            response.status_code = status.HTTP_200_OK
        else:
            response.status_code = status.HTTP_201_CREATED
        return {
            "incident_id": incident_id,
            "action": result.action,
            "provider_reference": result.provider_reference,
            "status": result.status,
            "duplicate": duplicate,
        }
