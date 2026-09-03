from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.replay import MerchantReplayControl
from app.db.tables import (
    IncidentAuditEvent,
    IncidentPaymentEvent,
    PaymentEvent,
    PaymentIncident,
)
from app.domain.incidents import (
    build_incident_evidence_bundle,
    correlate_payment,
    incident_case_chain,
    link_case_to_incident,
    link_event_to_incident,
)
from app.incidents.replay import (
    advance_replay,
    replay_status,
    reset_replay,
    run_replay,
    start_replay,
)
from simulator.merchant_day import DEFAULT_REPLAY_ID, DEFAULT_SEED

router = APIRouter(prefix="/api/v1", tags=["incidents"])


class IncidentLinkRequest(BaseModel):
    event_id: str | None = None
    case_id: str | None = None


def _integer(value: object | None) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _linked_payment_facts(session: Session, incident_id: str) -> dict[str, int]:
    rows = session.scalars(
        select(PaymentEvent)
        .join(
            IncidentPaymentEvent,
            IncidentPaymentEvent.event_id == PaymentEvent.event_id,
        )
        .where(IncidentPaymentEvent.incident_id == incident_id)
    ).all()
    failed = [event for event in rows if event.status == "failed"]
    return {
        "linked_attempt_count": len(rows),
        "failed_attempt_count": len(failed),
        "amount_affected_paise": sum(event.amount for event in failed),
    }


def _incident_summary(
    incident: PaymentIncident,
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    cohort = incident.cohort_filter or {}
    evidence = incident.detection_evidence_json or {}
    trigger = evidence.get("trigger_snapshot")
    trigger = trigger if isinstance(trigger, dict) else {}
    linked = (
        _linked_payment_facts(session, incident.incident_id)
        if session is not None
        else {
            "linked_attempt_count": 0,
            "failed_attempt_count": 0,
            "amount_affected_paise": 0,
        }
    )
    estimated_recoverable = max(
        _integer(evidence.get("peak_estimated_recoverable_paise")),
        _integer(trigger.get("estimated_recoverable_paise")),
        _integer(evidence.get("estimated_recoverable_paise")),
    )
    return {
        "incident_id": incident.incident_id,
        "state": incident.state,
        "opened_at": incident.opened_at.isoformat(),
        "updated_at": incident.updated_at.isoformat(),
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        "detection_version": incident.detection_version,
        "cohort_filter": incident.cohort_filter,
        "provider": cohort.get("provider"),
        "method": cohort.get("method"),
        "source_kind": cohort.get("source_kind"),
        "baseline_metrics": incident.baseline_metrics,
        "observed_metrics": incident.observed_metrics,
        "affected_attempt_count": incident.affected_attempt_count,
        "linked_attempt_count": linked["linked_attempt_count"],
        "failed_attempt_count": linked["failed_attempt_count"],
        "amount_affected_paise": linked["amount_affected_paise"],
        "estimated_amount_at_risk": incident.estimated_amount_at_risk,
        "peak_estimated_amount_at_risk_paise": max(
            _integer(evidence.get("peak_estimated_amount_at_risk_paise")),
            _integer(trigger.get("estimated_amount_at_risk_paise")),
            _integer(incident.estimated_amount_at_risk),
        ),
        "estimated_recoverable_paise": estimated_recoverable,
        "confidence": incident.confidence,
        "peak_confidence": max(
            float(evidence.get("peak_confidence", 0.0))
            if isinstance(evidence.get("peak_confidence"), (int, float))
            else 0.0,
            float(trigger.get("confidence", 0.0))
            if isinstance(trigger.get("confidence"), (int, float))
            else 0.0,
            incident.confidence,
        ),
        "provenance_summary": incident.provenance_summary_json,
        "resolution_reason": evidence.get("resolution_reason"),
        "analysis_reference": incident.analysis_reference,
        "recommendation_reference": incident.recommendation_reference,
    }


@router.get("/incidents")
def list_incidents(
    request: Request,
    include_replay_history: bool = Query(default=False),
) -> list[dict[str, Any]]:
    with request.app.state.session_factory() as session:
        incidents = list(
            session.scalars(
                select(PaymentIncident).order_by(PaymentIncident.opened_at.desc())
            ).all()
        )
        if not include_replay_history:
            active_runs = {
                control.replay_id: control.active_run_id
                for control in session.scalars(select(MerchantReplayControl)).all()
            }
            incidents = [
                incident
                for incident in incidents
                if _is_visible_incident(incident, active_runs)
            ]
        return [_incident_summary(incident, session=session) for incident in incidents]


def _is_visible_incident(
    incident: PaymentIncident,
    active_runs: dict[str, str],
) -> bool:
    replay_id = incident.cohort_filter.get("replay_id")
    if not isinstance(replay_id, str):
        return True
    return incident.cohort_filter.get("run_id") == active_runs.get(replay_id)


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
        detail = _incident_summary(incident, session=session)
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


@router.get("/replay/status")
def get_replay_status(
    request: Request,
    replay_id: str = Query(default=DEFAULT_REPLAY_ID, min_length=1, max_length=64),
    seed: int = Query(default=DEFAULT_SEED, ge=0),
) -> dict[str, object]:
    with request.app.state.session_factory() as session:
        try:
            return replay_status(session, replay_id=replay_id, seed=seed)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/replay/reset")
def reset_merchant_replay(
    request: Request,
    replay_id: str = Query(default=DEFAULT_REPLAY_ID, min_length=1, max_length=64),
) -> dict[str, object]:
    with request.app.state.session_factory() as session:
        try:
            result = reset_replay(session, replay_id=replay_id)
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=422, detail=str(error)) from error
        session.commit()
        return result


@router.post("/replay/advance", status_code=status.HTTP_201_CREATED)
def advance_merchant_replay(
    request: Request,
    replay_id: str = Query(default=DEFAULT_REPLAY_ID, min_length=1, max_length=64),
    seed: int = Query(default=DEFAULT_SEED, ge=0),
    count: int = Query(default=6, ge=1, le=300),
    scenario: Literal["primary", "healthy"] = Query(default="primary"),
) -> dict[str, object]:
    with request.app.state.session_factory() as session:
        try:
            result = advance_replay(
                session,
                replay_id=replay_id,
                seed=seed,
                count=count,
                scenario=scenario,
            )
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        session.commit()
        return result


@router.post("/replay/start", status_code=status.HTTP_201_CREATED)
def start_merchant_replay(
    request: Request,
    replay_id: str = Query(default=DEFAULT_REPLAY_ID, min_length=1, max_length=64),
    seed: int = Query(default=DEFAULT_SEED, ge=0),
) -> dict[str, object]:
    with request.app.state.session_factory() as session:
        try:
            result = start_replay(session, replay_id=replay_id, seed=seed)
        except (RuntimeError, ValueError) as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        session.commit()
        return result


@router.post("/replay/run", status_code=status.HTTP_201_CREATED)
def run_merchant_replay(
    request: Request,
    replay_id: str = Query(default=DEFAULT_REPLAY_ID, min_length=1, max_length=64),
    seed: int = Query(default=DEFAULT_SEED, ge=0),
    scenario: Literal["primary", "healthy"] = Query(default="primary"),
) -> dict[str, object]:
    with request.app.state.session_factory() as session:
        try:
            result = run_replay(
                session,
                replay_id=replay_id,
                seed=seed,
                scenario=scenario,
            )
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        session.commit()
        return result
