from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.db.tables import (
    ActionEvent,
    AuditEvent,
    Customer,
    PolicyChangeAudit,
    PolicyConfiguration,
    RecoveryCase,
)
from app.domain.models import MockInboxReplyRequest, PolicySettingsRequest
from app.policy import get_policy_configuration

router = APIRouter(prefix="/api/v1", tags=["operator controls"])
TERMINAL_STATES = {"recovered", "stopped", "escalated"}


def _role(request: Request) -> str:
    return (
        "business_owner"
        if getattr(request.app.state, "sentinel_owner_actor_id", None)
        else "operations_worker"
    )


@router.get("/policy-settings")
def get_policy_settings(request: Request) -> dict:
    with request.app.state.session_factory() as session:
        return _policy_configuration_response(get_policy_configuration(session, request.app.state))


@router.put("/policy-settings")
def update_policy_settings(payload: PolicySettingsRequest, request: Request) -> dict:
    if _role(request) != "business_owner":
        raise HTTPException(status_code=403, detail="business owner role required")
    with request.app.state.session_factory() as session:
        current = get_policy_configuration(session, request.app.state)
        configuration = session.get(PolicyConfiguration, "active")
        if configuration is None:
            configuration = PolicyConfiguration(
                configuration_id="active",
                version=current.version + 1,
                quiet_hours_start=payload.quiet_hours_start,
                quiet_hours_end=payload.quiet_hours_end,
                contact_limit=payload.contact_limit,
                kill_switch=payload.kill_switch,
                mock_identity=payload.mock_identity,
            )
            session.add(configuration)
        else:
            configuration.version += 1
            configuration.quiet_hours_start = payload.quiet_hours_start
            configuration.quiet_hours_end = payload.quiet_hours_end
            configuration.contact_limit = payload.contact_limit
            configuration.kill_switch = payload.kill_switch
            configuration.mock_identity = payload.mock_identity
            configuration.updated_at = datetime.now(UTC)
        session.add(
            PolicyChangeAudit(
                version=configuration.version,
                actor_role="business_owner",
                payload=payload.model_dump(),
            )
        )
        session.commit()
        return _policy_configuration_response(get_policy_configuration(session, request.app.state))


@router.post("/mock-inbox/{provider_reference}/reply")
def reply_to_mock_message(
    provider_reference: str, payload: MockInboxReplyRequest, request: Request
) -> dict:
    with request.app.state.session_factory() as session:
        action = session.scalar(
            select(ActionEvent).where(ActionEvent.provider_reference == provider_reference)
        )
        if action is None or action.tool not in {"contact", "promise"}:
            raise HTTPException(status_code=404, detail="mock message not found")
        if action.reply is not None:
            raise HTTPException(status_code=409, detail="mock message already has a reply")
        case = session.get(RecoveryCase, action.case_id)
        if case is None:
            raise HTTPException(status_code=409, detail="recovery case not found")
        action.reply = payload.reply
        action.replied_at = datetime.now(UTC)
        session.add(
            AuditEvent(
                case_id=case.case_id,
                event_type="mock.reply",
                payload={"action_id": action.action_id, "reply": payload.reply},
            )
        )
        if payload.reply == "pay":
            session.add(
                AuditEvent(
                    case_id=case.case_id,
                    event_type="mock.customer_intent_to_pay",
                    payload={
                        "action_id": action.action_id,
                        "provider_reference": provider_reference,
                        "actual_recovered_amount_paise": 0,
                        "awaiting_provider_evidence": True,
                    },
                )
            )
        elif payload.reply in {"promise", "help"}:
            case.state = "escalated"
            session.add(
                AuditEvent(
                    case_id=case.case_id,
                    event_type="case.escalated",
                    payload={"reason": f"mock_{payload.reply}"},
                )
            )
        elif payload.reply == "opt_out":
            _withdraw_consent(session, case.customer_id)
        session.commit()
        return {"provider_reference": provider_reference, "reply": payload.reply}


def _withdraw_consent(session, customer_id: str | None) -> None:
    if customer_id is None:
        return
    customer = session.get(Customer, customer_id)
    if customer is not None:
        customer.consent = False
    cases = session.scalars(
        select(RecoveryCase).where(RecoveryCase.customer_id == customer_id)
    ).all()
    for case in cases:
        if case.state in TERMINAL_STATES:
            continue
        case.state = "stopped"
        case.stop_reason = "consent_withdrawn"
        session.add(
            AuditEvent(
                case_id=case.case_id,
                event_type="case.stopped",
                payload={"reason": "consent_withdrawn"},
            )
        )


def _policy_configuration_response(configuration) -> dict:
    return {
        "version": configuration.version,
        "policy_version": configuration.policy_version,
        "quiet_hours_start": configuration.quiet_hours_start,
        "quiet_hours_end": configuration.quiet_hours_end,
        "contact_limit": configuration.contact_limit,
        "kill_switch": configuration.kill_switch,
        "mock_identity": configuration.mock_identity,
    }
