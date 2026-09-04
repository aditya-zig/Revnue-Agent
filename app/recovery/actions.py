import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.tables import ActionEvent, AuditEvent, Decision, RecoveryCase
from app.domain.enums import CaseState
from app.domain.models import ActionResponse
from app.domain.state_machine import transition_case
from app.integrations.razorpay import PAYMENT_LINK_PROVIDER_ERROR, RazorpayProviderError
from app.policy import evaluate_policy


class ProviderError(Exception):
    def __init__(self, public_message: str, *, diagnostic: str):
        super().__init__(public_message)
        self.public_message = public_message
        self.diagnostic = diagnostic


def _provider_diagnostic(error: Exception) -> str:
    if isinstance(error, RazorpayProviderError):
        diagnostic = error.diagnostic
        if diagnostic.startswith("payment_link_http_status="):
            code = diagnostic.removeprefix("payment_link_http_status=")
            if code.isdigit():
                return diagnostic
        if diagnostic.startswith("payment_link_provider_exception="):
            exception_name = diagnostic.removeprefix("payment_link_provider_exception=")
            if exception_name.isidentifier():
                return diagnostic
        if diagnostic in {
            "payment_link_provider_response_invalid",
            "payment_link_provider_response_missing_id",
        }:
            return diagnostic
    # Injected providers are untrusted test seams too; never copy their message.
    return f"payment_link_provider_exception={type(error).__name__}"


def _decision_with_approval(
    session: Session,
    case_id: str,
    action: str,
    idempotency_key: str,
    *,
    granted: bool,
) -> Decision | None:
    decision_id = f"decision_{hashlib.sha256(idempotency_key.encode()).hexdigest()}"
    decision = session.get(Decision, decision_id)
    if decision is None:
        return None
    if decision.case_id != case_id or decision.selected_action != action:
        return None
    approval = decision.reason_json.get("approval")
    if not isinstance(approval, dict) or approval.get("granted") is not granted:
        return None
    return decision


def execute_action(
    session: Session,
    case: RecoveryCase,
    action: str,
    idempotency_key: str,
    now: datetime,
    quiet_hours_start: int,
    quiet_hours_end: int,
    create_payment_link: Callable[[int, str], str],
    kill_switch: bool = False,
    contact_limit: int = 3,
    policy_version: str = "v1",
) -> tuple[ActionResponse, bool]:
    existing = session.scalar(
        select(ActionEvent).where(ActionEvent.idempotency_key == idempotency_key)
    )
    if existing:
        if existing.case_id != case.case_id or existing.tool != action:
            raise ValueError("idempotency key belongs to another action")
        return (
            ActionResponse(
                action=existing.tool,
                provider_reference=existing.provider_reference,
                status=existing.status,
            ),
            True,
        )

    if case.state != CaseState.ELIGIBLE:
        raise PermissionError(["invalid_state"])

    pending_approval = _decision_with_approval(
        session, case.case_id, action, idempotency_key, granted=False
    )
    approved_decision = _decision_with_approval(
        session, case.case_id, action, idempotency_key, granted=True
    )
    if pending_approval is not None or approved_decision is None:
        session.add(
            AuditEvent(
                case_id=case.case_id,
                event_type="action.blocked",
                payload={
                    "action": action,
                    "idempotency_key": idempotency_key,
                    "reasons": ["approval_required"],
                    "decision_id": (
                        pending_approval.decision_id
                        if pending_approval is not None
                        else approved_decision.decision_id
                        if approved_decision is not None
                        else None
                    ),
                },
            )
        )
        session.commit()
        raise PermissionError(["approval_required"])

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
    if action not in policy.allowed_actions:
        session.add(
            AuditEvent(
                case_id=case.case_id,
                event_type="action.blocked",
                payload={
                    "action": action,
                    "idempotency_key": idempotency_key,
                    "reasons": policy.blocked_reasons.get(action, ["action_not_allowed"]),
                },
            )
        )
        session.commit()
        raise PermissionError(policy.blocked_reasons.get(action, ["action_not_allowed"]))

    input_hash = hashlib.sha256(
        json.dumps({"action": action, "amount": case.amount_at_risk}, sort_keys=True).encode()
    ).hexdigest()
    claimed = cast(
        CursorResult[Any],
        session.execute(
            update(RecoveryCase)
            .where(
                RecoveryCase.case_id == case.case_id,
                RecoveryCase.state == CaseState.ELIGIBLE,
            )
            .values(state=CaseState.ACTION_SELECTED)
        ),
    )
    if claimed.rowcount != 1:
        session.rollback()
        existing = session.scalar(
            select(ActionEvent).where(ActionEvent.idempotency_key == idempotency_key)
        )
        if existing:
            if existing.case_id != case.case_id or existing.tool != action:
                raise ValueError("idempotency key belongs to another action")
            return (
                ActionResponse(
                    action=existing.tool,
                    provider_reference=existing.provider_reference,
                    status=existing.status,
                ),
                True,
            )
        raise PermissionError(["invalid_state"])
    session.refresh(case)
    session.add(
        AuditEvent(
            case_id=case.case_id,
            event_type="case.action_selected",
            payload={"action": action, "idempotency_key": idempotency_key},
        )
    )
    action_event = ActionEvent(
        action_id=f"action_{hashlib.sha256(idempotency_key.encode()).hexdigest()}",
        case_id=case.case_id,
        idempotency_key=idempotency_key,
        tool=action,
        input_hash=input_hash,
        status="processing",
    )
    session.add(action_event)
    session.add(
        AuditEvent(
            case_id=case.case_id,
            event_type="action.started",
            payload={"action": action, "idempotency_key": idempotency_key},
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(ActionEvent).where(ActionEvent.idempotency_key == idempotency_key)
        )
        if existing and existing.case_id == case.case_id and existing.tool == action:
            return (
                ActionResponse(
                    action=existing.tool,
                    provider_reference=existing.provider_reference,
                    status=existing.status,
                ),
                True,
            )
        raise ValueError("idempotency key belongs to another action")

    status = "pending" if action == "retry" else "completed"
    provider_reference = f"mock_{action}_{idempotency_key}"
    provider_reference_id: str | None = None
    if action == "payment_link":
        try:
            provider_result = create_payment_link(case.amount_at_risk, idempotency_key)
            provider_reference = str(provider_result)
            provider_reference_id = getattr(provider_result, "provider_id", None)
            if not isinstance(provider_reference_id, str) or not provider_reference_id:
                raise ValueError("provider response missing durable provider ID")
        except Exception as error:
            diagnostic = _provider_diagnostic(error)
            action_event.status = "failed"
            session.add(
                AuditEvent(
                    case_id=case.case_id,
                    event_type="action.failed",
                    payload={
                        "action": action,
                        "idempotency_key": idempotency_key,
                        "reason": PAYMENT_LINK_PROVIDER_ERROR,
                        "diagnostic": diagnostic,
                    },
                )
            )
            transition_case(
                session,
                case,
                CaseState.ESCALATED,
                payload_extra={"owner": "business_owner", "reason": "provider_failure"},
            )
            session.commit()
            raise ProviderError(
                PAYMENT_LINK_PROVIDER_ERROR,
                diagnostic=diagnostic,
            ) from error

    transition_case(session, case, CaseState.AWAITING_OUTCOME)
    if action == "escalate":
        transition_case(session, case, CaseState.ESCALATED)

    action_event.status = status
    action_event.provider_reference = provider_reference
    action_event.provider_reference_id = provider_reference_id
    session.add(
        AuditEvent(
            case_id=case.case_id,
            event_type=f"action.{status}",
            payload={
                "action": action,
                "idempotency_key": idempotency_key,
                "provider_reference": provider_reference,
            },
        )
    )
    session.commit()
    return (
        ActionResponse(action=action, provider_reference=provider_reference, status=status),
        False,
    )
